"""Source locations over observed bytes, never model-generated structure.

Offsets are zero-based UTF-8 bytes, end-exclusive. Lines are one-based,
end-inclusive, counting LF (including CRLF). Unsupported syntax stays raw.
"""
import ast
import bisect
import csv
import io
import json
import re
import tomllib
from pathlib import PurePosixPath

MAX_SEGMENTS = 500

def index(content: bytes, path: str):
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return [], 'binary'
    lines = re.findall(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+', text)  # LF, CRLF and CR only, like ast and CommonMark
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line.encode('utf-8')))
    output = []
    def add(name, kind, start, end, parser, certainty='structural'):
        if len(output) >= MAX_SEGMENTS: return
        output.append(dict(id=f'{kind}:{start}:{end}', name=name[:240], kind=kind,
                           start_byte=start, end_byte=end,
                           start_line=bisect.bisect_right(starts, start),
                           end_line=bisect.bisect_right(starts, max(start, end-1)),
                           parser=parser, certainty=certainty))
    add('Whole file', 'file', 0, len(content), 'utf8-range-v1', 'raw')
    suffix = PurePosixPath(path).suffix.lower()
    state = 'parsed'
    try:
        if suffix == '.py':
            tree = ast.parse(text)
            def visit(node, prefix=''):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = prefix + child.name
                        first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                        add(name, 'symbol', starts[first-1], starts[child.end_lineno-1]+child.end_col_offset, 'python-ast-v1')
                        visit(child, name+'.')
                    else: visit(child, prefix)
            visit(tree)
        elif suffix in ('.md', '.markdown'):
            headings = []; fence_char = None; fence_length = 0
            for n, line in enumerate(lines):
                body = line[:-2] if line.endswith('\r\n') else line[:-1] if line.endswith(('\r', '\n')) else line
                if fence_char is not None:
                    closing = re.match(r'^ {0,3}([' + re.escape(fence_char) + r']+)[ \t]*$', body)
                    if closing and len(closing[1]) >= fence_length:
                        fence_char = None; fence_length = 0
                    continue
                opening = re.match(r'^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$', body)
                if opening and not (opening['marker'][0] == '`' and '`' in opening['info']):
                    fence_char = opening['marker'][0]; fence_length = len(opening['marker'])
                    continue
                m = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
                if m: headings.append((n, len(m[1]), m[2]))
                elif n and re.match(r'^ {0,3}(=+|-+)\s*$', line) and lines[n-1].strip():
                    headings.append((n-1, 1 if '=' in line else 2, lines[n-1].strip()))
            stack = []; counts = {}
            for j, (n, level, name) in enumerate(headings):
                while stack and stack[-1][0] >= level: stack.pop()
                stack.append((level, name))
                label = ' / '.join(v for _,v in stack)
                counts[label] = counts.get(label, 0)+1
                end = next((starts[i] for i,l,_ in headings[j+1:] if l<=level), len(content))
                add(f'{label} [{counts[label]}]', 'heading', starts[n], end, 'markdown-headings-v1')
        elif suffix == '.json':
            json.loads(text)  # Validate the complete document first.
            decoder = json.JSONDecoder(); positions = [0]
            for c in text: positions.append(positions[-1]+len(c.encode('utf-8')))
            def whitespace(i):
                while i<len(text) and text[i].isspace(): i+=1
                return i
            def walk(i, pointer='', depth=0):
                i=whitespace(i); start=i
                if depth>32: raise ValueError('depth')
                if text[i] in '[{':
                    obj=text[i]=='{'; i=whitespace(i+1); k=0
                    while text[i] not in ']}':
                        if obj:
                            key,end=decoder.raw_decode(text,i); i=whitespace(end)+1
                            part=str(key).replace('~','~0').replace('/','~1')
                        else: part=str(k)
                        i=walk(i,pointer+'/'+part,depth+1); i=whitespace(i); k+=1
                        if text[i]==',': i=whitespace(i+1)
                    end=i+1
                else: _,end=decoder.raw_decode(text,i)
                add(pointer or '/', 'json_pointer', positions[start], positions[end], 'json-pointer-v1')
                return end
            walk(0)
        elif suffix in ('.tex', '.latex'):
            matches = list(re.finditer(r'(?m)^\s*\\(part|chapter|section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{([^}\n]*)\}', text))
            for n,m in enumerate(matches):
                end=matches[n+1].start() if n+1<len(matches) else len(text)
                add(m[2], 'section', len(text[:m.start()].encode()), len(text[:end].encode()), 'latex-source-spans-v1')
            environments=[]
            for m in re.finditer(r'\\(begin|end)\{([A-Za-z*]+)\}',text):
                if m[1]=='begin' and len(environments)<32: environments.append((m[2],m.start()))
                elif m[1]=='end' and environments and environments[-1][0]==m[2]:
                    name,start=environments.pop()
                    add(name,'environment',len(text[:start].encode()),len(text[:m.end()].encode()),'latex-source-spans-v1','raw')
            for m in re.finditer(r'\\(?:input|include)\{([^}\n]+)\}',text):
                add('Input locator: '+m[1],'input_locator',len(text[:m.start()].encode()),len(text[:m.end()].encode()),'latex-source-spans-v1','raw')
            state='partial'  # Macro expansion and included files are not interpreted.
        elif suffix in ('.yaml','.yml'):
            import yaml
            try: tree=yaml.compose(text,Loader=yaml.SafeLoader)
            except yaml.YAMLError: return output[:1], 'parse_error'
            positions=[0]
            for c in text: positions.append(positions[-1]+len(c.encode('utf-8')))
            seen=set()
            def yaml_walk(node,pointer='',depth=0):
                if depth>32 or id(node) in seen: return
                seen.add(id(node))
                if not node.tag.startswith('tag:yaml.org,2002:'): raise ValueError('Unsupported YAML tag')
                add(pointer or '/', 'yaml_key', positions[node.start_mark.index], positions[node.end_mark.index], 'pyyaml-safe-locations-v1')
                if isinstance(node,yaml.MappingNode):
                    for key,value in node.value:
                        if not isinstance(key,yaml.ScalarNode): raise ValueError('Complex YAML key')
                        yaml_walk(value,pointer+'/'+str(key.value).replace('~','~0').replace('/','~1'),depth+1)
                elif isinstance(node,yaml.SequenceNode):
                    for i,value in enumerate(node.value): yaml_walk(value,pointer+'/'+str(i),depth+1)
            if tree: yaml_walk(tree)
        elif suffix == '.toml':
            tomllib.loads(text)
            for n,line in enumerate(lines):
                m=re.match(r'\s*(\[.+\]|[^#=]+)\s*(?:=|$)',line)
                if m: add(m[1].strip(), 'key_source', starts[n], starts[n+1], 'tomllib-raw-locations-v1', 'raw')
            state='partial'
        elif suffix in ('.csv', '.tsv'):
            reader=csv.reader(io.StringIO(text,newline=''),delimiter='\t' if suffix=='.tsv' else ',')
            prior=0
            for n,row in enumerate(reader):
                end=starts[min(reader.line_num,len(starts)-1)]
                add('Schema: '+', '.join(row) if n==0 else f'Row {n}', 'table_row', prior, end, 'csv-v1')
                prior=end
                if len(output)>=MAX_SEGMENTS: state='partial'; break
        elif suffix in ('.log', '.txt'):
            for n in range(0,len(lines),100):
                add(f'Lines {n+1}–{min(n+100,len(lines))}', 'log_range', starts[n], starts[min(n+100,len(lines))], 'utf8-log-v1','raw')
        else:
            state='raw_fallback'
    except (SyntaxError, ValueError, RecursionError, csv.Error):
        return output[:1], 'parse_error'
    if len(output)>=MAX_SEGMENTS: state='partial'
    return output, state
