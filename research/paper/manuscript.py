from __future__ import annotations

import json
import io
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
import httpx

from research.paper.evidence import collect_evidence, manuscript_prompt

def tex(value):
    mapping = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    mapping.update({'±': r'\ensuremath{\pm}', '×': r'\ensuremath{\times}', '−': '-',
                    '≤': r'\ensuremath{\leq}', '≥': r'\ensuremath{\geq}', '≈': r'\ensuremath{\approx}',
                    '≠': r'\ensuremath{\ne}', '∞': r'\ensuremath{\infty}', '\u00a0': ' ',
                    '′': r'\ensuremath{^{\prime}}', '″': r'\ensuremath{^{\prime\prime}}',
                    '²': r'\ensuremath{^{2}}', '³': r'\ensuremath{^{3}}',
                    '’': "'", '‘': '`', '“': '``', '”': "''"})
    for symbol, command in {'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'θ': 'theta',
                            'λ': 'lambda', 'μ': 'mu', 'π': 'pi', 'ρ': 'rho', 'σ': 'sigma',
                            'τ': 'tau', 'φ': 'phi', 'χ': 'chi', 'Δ': 'Delta', 'Σ': 'Sigma'}.items():
        mapping[symbol] = '\\ensuremath{\\' + command + '}'
    return "".join(mapping.get(c, c) for c in str(value))


def _macro_key(*parts):
    # TeX command names contain letters only, including numerical indices.
    digits = dict(zip("0123456789", ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]))
    value = "".join(str(p).title().replace("_", "") for p in parts)
    return "Result" + "".join(digits.get(c, c) for c in value if c.isalnum())


def generate_paper(research_dir, output_dir, title=None, template="article", *, evidence=None, draft=None, layout=None):
    """Render supplied authored/model prose against actual completed-run evidence.

    There is no topic-specific template or generated-result fallback. Callers
    record the real provider response separately when the draft was model-written.
    """
    from research.paper.evidence import write_manuscript
    if evidence is None or draft is None:
        raise ValueError('Generic paper generation requires actual run evidence and a supplied draft; connect a model or provide authored prose')
    return write_manuscript(output_dir, evidence, draft, title, template, layout)


def apply_template(directory, template="article"):
    """Apply an optional official venue template without implying submission.

    Only the conference's own .sty/.bst assets are fetched. natbib/fancyhdr come
    from the user's installed TeX distribution, preserving upstream packaging.
    """
    directory = Path(directory)
    source_path = directory / "paper.tex"
    source = source_path.read_text()
    if template == "article":
        source = source.replace(r"\usepackage[T1]{fontenc}" + "\n", "")
        source = source.replace(r"\documentclass{article}" + "\n" + r"\usepackage{iclr2027_conference,times}", r"\documentclass[11pt]{article}" + "\n" + r"\usepackage[margin=1in]{geometry}")
        source = source.replace(r"\bibliographystyle{iclr2027_conference}", r"\bibliographystyle{plainnat}")
        source = source.replace("\n" + r"\lhead{Research draft -- ICLR 2027 format}", "")
        info = {"template": "article", "submission_status": "research_draft"}
    elif template == "iclr2027":
        url = "https://media.iclr.cc/Conferences/ICLR2027/iclr-2027-style-files.zip"
        filenames = ["iclr2027_conference.sty", "iclr2027_conference.bst"]
        if not all((directory / name).exists() for name in filenames):
            response = httpx.get(url, timeout=45, follow_redirects=True)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                for name in filenames:
                    (directory / name).write_bytes(archive.read("iclr2027/" + name))
        source = source.replace(r"\documentclass[11pt]{article}", r"\documentclass{article}" + "\n" + r"\usepackage[T1]{fontenc}" + "\n" + r"\usepackage{iclr2027_conference,times}")
        source = source.replace(r"\usepackage[margin=1in]{geometry}" + "\n", "")
        source = source.replace(r"\setlength{\parskip}{0.35em}" + "\n", "")
        source = source.replace(r"\bibliographystyle{plainnat}", r"\bibliographystyle{iclr2027_conference}")
        if r"\lhead{Research draft -- ICLR 2027 format}" not in source:
            source = source.replace(r"\maketitle", r"\maketitle" + "\n" + r"\lhead{Research draft -- ICLR 2027 format}")
        # Official style also embeds a submission assertion beneath the author.
        # Patch it in the user's document; preserve the downloaded style verbatim.
        author_patch = "\n".join([r"\usepackage{etoolbox}", r"\makeatletter", r"\patchcmd{\@maketitle}{Paper under double-blind review}{Reproducible empirical draft}{}{}", r"\makeatother"])
        if "Reproducible empirical draft}{}{}" not in source:
            source = source.replace(r"\begin{document}", author_patch + "\n" + r"\begin{document}")
        info = {"template": "iclr2027", "source_url": url, "guidelines_url": "https://iclr.cc/Conferences/2027/AuthorGuidelines", "ai_policy_url": "https://iclr.cc/Conferences/2027/AIPolicyForAuthors", "submission_status": "research_draft", "assets": filenames, "license_note": "Official conference assets downloaded for the requested author-template use; original notices preserved. Third-party TeX dependencies use the installed distribution."}
    else:
        raise ValueError("Supported templates are article and iclr2027")
    source_path.write_text(source)
    (directory / "template.json").write_text(json.dumps(info, indent=2))
    return info


def compile_paper(directory, source_name="paper.tex", timeout=180, *, repair_layout=False, evidence_root=None, _layout_attempt=1):
    from research.agents.artifacts import file_identity, matches_record, record_file
    from research.paper.layout import compile_preflight
    directory = Path(directory).resolve()
    source = (directory / source_name).resolve()
    if source.parent != directory or source.suffix != ".tex" or not source.is_file():
        raise ValueError("Compile a .tex source in the paper directory")
    tectonic, pdflatex = shutil.which("tectonic"), shutil.which("pdflatex")
    records = Path(evidence_root).resolve() / '.forest-compile' if evidence_root is not None and (tectonic or pdflatex) else None
    source_record = record_file(source, records) if records is not None else None
    logs = []
    if tectonic:
        commands = [[tectonic, "--keep-logs", "--keep-intermediates", "--outdir", str(directory), source.name]]
    elif pdflatex:
        commands = [[pdflatex, "-no-shell-escape", "-interaction=nonstopmode", "-file-line-error", source.name]]
        if "\\bibliography" in source.read_text():
            bibtex_binary = shutil.which("bibtex")
            if not bibtex_binary:
                raise RuntimeError("BibTeX is required for this manuscript")
            commands.extend([[bibtex_binary, source.stem], commands[0], commands[0]])
        else:
            # Cross-reference numbers need the auxiliary file from the first pass.
            commands.append(commands[0])
    else:
        report = compile_preflight(directory, source_name, False, '', actual_compilation=False)
        return {"status": "unavailable", "pdf_path": None, "log": "No tectonic or pdflatex executable found", "errors": [{"message": "Install tectonic or a TeX distribution"}],
                "preflight": report, "preflight_path": str(directory / 'layout_preflight.json')}
    # Remove an old PDF so a failed compile cannot be mistaken for new output.
    pdf = directory / (source.stem + ".pdf")
    pdf.unlink(missing_ok=True)
    exit_code = 0
    previous_pdf = None
    for index, command in enumerate(commands, 1):
        if index == len(commands) and pdf.is_file():
            previous_pdf = file_identity(pdf)
        try:
            process = subprocess.run(command, cwd=directory, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            logs.append(process.stdout)
            exit_code = process.returncode
            if exit_code:
                break
        except subprocess.TimeoutExpired as error:
            logs.append(f"Compilation timed out after {timeout}s\n" + str(error.stdout or ""))
            exit_code = 124
            break
    # pdfLaTeX can exit successfully without replacing an earlier pass's PDF.
    success = exit_code == 0 and pdf.is_file() and file_identity(pdf) != previous_pdf
    if exit_code == 0 and not success:
        logs.append('The final compiler pass did not produce a current PDF.')
    evidence = {}
    if success and source_record:
        if not matches_record(source, source_record) or file_identity(source) != source_record['file_identity']:
            exit_code, success = 1, False
            logs.append('Manuscript source changed during compilation. Recompile the current source.')
        else:
            pdf_record = record_file(pdf, records)
            originals = {'paper_source': source_record, 'pdf_path': pdf_record}
            evidence['compile_evidence'] = {
                'paper_source': str(source), 'pdf_path': str(pdf), 'actual_compilation': True,
                'compile_originals': {key: str(Path(record['snapshot_path']).relative_to(records.parent))
                                      for key, record in originals.items()},
            }
    log = "\n".join(logs)
    (directory / "compile.log").write_text(log)
    errors = [{"line": int(match.group(1)), "message": match.group(2).strip()} for match in re.finditer(r"(?:\.tex:|^l\.)(\d+)[: ]\s*(.*)", log, re.M)]
    report = compile_preflight(directory, source_name, success, log)
    if repair_layout and success and _layout_attempt <= 3:
        distant = [item for item in report.get('visual_placements', [])
                   if item.get('page_distance', 0) > 1 and item.get('anchor_label')]
        original = source.read_text()
        revised = original
        repaired = []
        for item in distant:
            anchor = r'\label{' + item['anchor_label'] + '}'
            marker = '% FOREST placement repair before ' + item['anchor_label']
            if revised.count(anchor) == 1 and marker not in revised:
                # Flush accumulated earlier floats before this argument. A
                # barrier after every visual creates isolated, mostly blank
                # float pages and prevents later prose from filling the page.
                revised = revised.replace(anchor, marker + '\n' + r'\FloatBarrier' + '\n' + anchor, 1)
                repaired.append(item)
        if repaired:
            if r'\usepackage{placeins}' not in revised:
                revised = revised.replace(r'\begin{document}', r'\usepackage{placeins}' + '\n' + r'\begin{document}', 1)
            history = directory / 'layout_repairs'
            history.mkdir(exist_ok=True)
            (history / f'attempt-{_layout_attempt}-before.tex').write_text(original)
            (history / f'attempt-{_layout_attempt}.json').write_text(json.dumps({
                'reason': 'Actual compiled visual exceeded one page after its argumentative anchor.',
                'placements': repaired, 'preflight_before': report,
                'change': 'Flush earlier floats before the affected argumentative paragraph; preserve all text and evidence.'
            }, indent=2))
            source.write_text(revised)
            result = compile_paper(directory, source_name, timeout, repair_layout=True, evidence_root=evidence_root,
                                   _layout_attempt=_layout_attempt + 1)
            result['layout_repair_attempts'] = max(result.get('layout_repair_attempts', 0), _layout_attempt)
            result['layout_repair_history'] = str(history)
            return result
    return {"status": "completed" if success else "failed", "exit_code": exit_code, "pdf_path": str(pdf) if success else None, "log": log, "log_path": str(directory / "compile.log"), "errors": errors,
            "preflight": report, "preflight_path": str(directory / 'layout_preflight.json'), **evidence}


def check_paper(directory, source_name="paper.tex"):
    from research.paper.evidence import resolve_pointer
    directory = Path(directory)
    source = (directory / source_name).read_text()
    issues = []
    for match in re.finditer(r"@@[^@]+@@|TODO|TBD|FIXME", source):
        issues.append({"code": "placeholder", "line": source[:match.start()].count("\n") + 1, "message": match.group()})
    bibliography = (directory / "references.bib").read_text() if (directory / "references.bib").exists() else ""
    keys = set(re.findall(r"@\w+\s*\{\s*([^,]+),", bibliography))
    for match in re.finditer(r"\\cite\w*\{([^}]+)\}", source):
        for key in match.group(1).split(","):
            if key.strip() not in keys:
                issues.append({"code": "missing_citation", "message": key.strip()})
    for figure in re.findall(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}", source):
        if not (directory / figure).is_file():
            issues.append({"code": "missing_figure", "message": figure})
    labels = re.findall(r'\\label\{([^}]+)\}', source)
    for label in sorted({label for label in labels if labels.count(label) > 1}):
        issues.append({'code': 'duplicate_label', 'message': label})
    for label in sorted(set(re.findall(r'\\(?:ref|eqref|autoref)\{([^}]+)\}', source)) - set(labels)):
        issues.append({'code': 'undefined_reference', 'message': label})
    figure_binding_path = directory / 'figure_bindings.json'
    if figure_binding_path.is_file():
        for binding in json.loads(figure_binding_path.read_text()):
            for kind, relative in binding.get('artifacts', {}).items():
                companion = (directory / relative).resolve()
                if not companion.is_relative_to(directory.resolve()) or not companion.is_file():
                    issues.append({'code': 'missing_figure_companion', 'message': binding['figure_id'] + ':' + kind})
    if (directory / "bindings.json").is_file():
        macros_path = directory / 'results_macros.tex'
        macro_source = macros_path.read_text() if macros_path.is_file() else ''
        for binding in json.loads((directory / "bindings.json").read_text()):
            try:
                metric_file = (directory / binding['file']).resolve()
                if not metric_file.is_relative_to(directory.resolve()):
                    raise ValueError('Metric file must be within the paper bundle')
                value = resolve_pointer(json.loads(metric_file.read_text()), binding['pointer'])
                if value != binding["value"]:
                    issues.append({"code": "stale_metric", "message": binding["macro"], "expected": value, "bound": binding["value"]})
                if 'display' in binding:
                    literal = '\\newcommand{\\' + binding['macro'] + '}{' + binding['display'] + '}'
                    if literal not in macro_source:
                        issues.append({'code': 'stale_macro', 'message': binding['macro']})
            except (KeyError, IndexError, OSError, TypeError, ValueError):
                issues.append({"code": "missing_metric", "message": binding["macro"]})
    evidence_path = directory / 'evidence.json'
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text())
            if evidence.get('statistics', {}).get('recognized'):
                from research.analysis.presentation import attach_statistical_evidence
                from research.paper.tables import validate_statistical_tables
                for index, run in enumerate(evidence['runs']):
                    run['metrics'] = json.loads((directory / 'evidence' / f'run{index}' / 'metrics.json').read_text())
                rebound = attach_statistical_evidence({**evidence, 'metrics': [item for item in evidence['metrics'] if not item.get('artifact')]})
                saved = json.loads((directory / 'evidence' / 'statistical_results.json').read_text())
                if rebound['statistics'] != saved:
                    raise ValueError('Statistical artifact does not match its original measured inputs')
                validate_statistical_tables(json.loads((directory / 'draft.json').read_text()), rebound)
        except (KeyError, IndexError, OSError, TypeError, ValueError) as exc:
            issues.append({'code': 'statistical_identity', 'message': str(exc)})
    return {"issues": issues, "checked": ["placeholders", "citation_keys", "cross_references", "figure_files", "figure_companions", "bound_metrics"], "claim_verification": "numerical binding checks only; not an automated scientific endorsement"}


def export_paper(directory, target):
    directory = Path(directory)
    target = Path(target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in directory.rglob("*"):
            if path.is_file() and path.resolve() != target.resolve() and path.suffix not in {".aux", ".blg", ".log"}:
                archive.write(path, path.relative_to(directory))
    return str(target)
