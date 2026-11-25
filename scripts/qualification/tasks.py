"""Thirty executable Agent tasks and deterministic, external output oracles.

Fixtures are mathematical/file program inputs or actual saved public-data
execution outputs with provenance. They are not model responses, substituted
execution receipts, or prewritten task implementations.
"""
from __future__ import annotations
import csv
import io
import json
import math
import re
import statistics
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import permutations

VERSION = 5
ORACLE_REVISION = 3

@dataclass
class Task:
    id: str
    category: str
    prompt: str
    inputs: dict[str, str]
    expected: dict
    extra_outputs: tuple[str, ...] = ()

    @property
    def outputs(self):
        return ('solution.py', 'result.json', *self.extra_outputs)


def result_types(expected):
    def kind(value):
        if isinstance(value,bool): return 'boolean'
        if isinstance(value,(int,float)): return 'number'
        if isinstance(value,str): return 'string'
        if isinstance(value,list): return 'array'
        if isinstance(value,dict): return 'object'
        return 'null'
    return {key:kind(value) for key,value in expected.items()}


def task_instruction(task):
    return task.prompt+'\nresult.json must be a JSON object with this exact top-level key/type contract: '+json.dumps(result_types(task.expected),sort_keys=True)+'. Include every key with its stated JSON type; do not replace the object with prose or use different key names.\nRead input.json and the supplied files. Write your own solution.py, execute it through the Python/process tools, inspect result.json, then finish. The script must write result.json. Required artifacts: '+', '.join(task.outputs)+'. Do not edit the platform or the qualification harness. This is an implementation task; no idea analysis is requested.'


def make(ident, category, prompt, data, expected, files=None, extra=()):
    inputs = {'input.json': json.dumps(data, indent=2)}
    inputs.update(files or {})
    return Task(ident, category, prompt, inputs, expected, tuple(extra))


def corpus():
    tasks=[]
    def add(*args, **kwargs): tasks.append(make(*args, **kwargs))
    # Oracle uses trial division, not the implementation supplied by the Agent.
    primes=[n for n in range(2, 10001) if all(n%d for d in range(2, math.isqrt(n)+1))]
    add('01-prime-independence','math',
        'Compute all primes through inclusive limit using two independent algorithms: a sieve of Eratosthenes and trial division. Write separate sieve.py and trial.py implementations; neither may import or call the other or a shared primality helper. Execute both and compare their full prime lists. result.json must contain count, sum, first_10, last_10 and independent_agreement. Keep both source files and both full lists in sieve_primes.json and trial_primes.json. Do not use precomputed prime tables.',
        {'limit':10000}, {'count':len(primes),'sum':sum(primes),'first_10':primes[:10],'last_10':primes[-10:],'independent_agreement':True}, extra=('sieve.py','trial.py','sieve_primes.json','trial_primes.json'))
    add('02-rational-system','math','Solve A x = b exactly. Return x as a list of reduced fraction strings, including /1 for integers.',
        {'A':[[3,2,-1],[2,-2,4],[-1,0.5,-1]],'b':[1,-2,0]}, {'x':['1/1','-2/1','-2/1']})
    pairs=[[17,3120],[23,101],[101,1009],[14,29]]
    add('03-modular-inverses','math','Compute modular inverse of each a modulo m. Return inverses in input order.',{'pairs':pairs},{'inverses':[pow(a,-1,m) for a,m in pairs]})
    c=[3,-2,0,5,-1]; lo=-2; hi=3
    area=sum(Fraction(v,i+1)*(hi**(i+1)-lo**(i+1)) for i,v in enumerate(c))
    add('04-polynomial-calculus','math','Coefficients are in ascending power order. Return derivative_coefficients and exact definite_integral as reduced fraction p/q.',{'coefficients':c,'lower':lo,'upper':hi},{'derivative_coefficients':[i*v for i,v in enumerate(c)][1:],'definite_integral':f'{area.numerator}/{area.denominator}'})
    add('05-combinatorial-count','math','Enumerate permutations of 0..7. Return count with no fixed points, and lexicographically first and last such permutation.',{'n':8},{'count':14833,'first':[1,0,3,2,5,4,7,6],'last':[7,6,5,4,3,2,1,0]})
    add('06-exact-probability','math','Two fair six-sided dice are rolled. Conditional on sum >= 8, find the probability at least one die is 6. Return numerator and denominator of the reduced fraction, and favorable/eligible outcome counts.',{'sides':6,'minimum_sum':8},{'numerator':3,'denominator':5,'favorable':9,'eligible':15})
    intervals=[[9,12],[1,3],[2,6],[8,10],[15,18],[18,20],[-2,0]]
    add('07-merge-intervals','code','Merge closed overlapping or endpoint-touching intervals and return merged sorted by start.',{'intervals':intervals},{'merged':[[-2,0],[1,6],[8,12],[15,20]]})
    edges=[['a','b'],['a','c'],['b','d'],['c','d'],['c','e'],['d','f'],['e','f']]
    add('08-topological-order','code','Return the lexicographically smallest topological order of the directed acyclic graph.',{'edges':edges},{'order':['a','b','c','d','e','f']})
    add('09-shortest-path','code','Compute shortest directed path from s to t, returning distance and path. Edge weights are positive.',{'edges':[['s','a',4],['s','b',1],['b','a',2],['a','t',1],['b','t',9]],'source':'s','target':'t'},{'distance':4,'path':['s','b','a','t']})
    add('10-runlength-roundtrip','code','Run-length encode text into [character,count] pairs and decode it. Return runs and decoded.',{'text':'aaabbccccdddddeefffaaa'},{'runs':[[k,len(list(g))] for k,g in __import__('itertools').groupby('aaabbccccdddddeefffaaa')],'decoded':'aaabbccccdddddeefffaaa'})
    add('11-edit-distance','code','Compute Levenshtein distance (unit insertion/deletion/substitution) for each pair and return distances.',{'pairs':[['kitten','sitting'],['forest','forms'],['','abc'],['research','research']]},{'distances':[3,2,3,0]})
    add('12-stable-dedup','code','Deduplicate records by id, keeping the last value but retaining the order in which ids first appeared. Return records.',{'records':[{'id':'b','v':2},{'id':'a','v':1},{'id':'b','v':7},{'id':'c','v':3},{'id':'a','v':9}]},{'records':[{'id':'b','v':7},{'id':'a','v':9},{'id':'c','v':3}]})
    csv_text='group,value,weight\na,2,1\nb,10,2\na,4,3\nb,14,1\na,8,2\nb,8,1\n'
    add('13-weighted-groups','data','Read observations.csv. Return groups mapping each group to n, sum and weighted_mean.',{}, {'groups':{'a':{'n':3,'sum':14,'weighted_mean':5},'b':{'n':3,'sum':32,'weighted_mean':10.5}}}, {'observations.csv':csv_text})
    pred={'labels':[0,1,1,0,1,0,0,1],'probabilities':[.1,.8,.4,.2,.95,.7,.3,.6]}
    add('14-prediction-metrics','data','Compute Brier score (mean squared probability error), log_loss (natural logs), and TP, FP, TN, FN at >=0.5. Return brier, log_loss and confusion.',pred,{'brier':sum((p-y)**2 for p,y in zip(pred['probabilities'],pred['labels']))/8,'log_loss':-sum(y*math.log(p)+(1-y)*math.log(1-p) for p,y in zip(pred['probabilities'],pred['labels']))/8,'confusion':{'tp':3,'fp':1,'tn':3,'fn':1}})
    rows=[{'id':i,'baseline':v,'candidate':w} for i,(v,w) in enumerate([(4,3),(5,3),(6,7),(8,6),(7,6),(2,2)],1)]
    diff=[r['baseline']-r['candidate'] for r in rows]
    add('15-paired-differences','data','Compute paired improvement baseline minus candidate. Return n, mean_improvement, sample_sd (ddof=1), improved, tied and worse.',{'records':rows},{'n':6,'mean_improvement':statistics.mean(diff),'sample_sd':statistics.stdev(diff),'improved':4,'tied':1,'worse':1})
    add('16-missing-data','data','Compute count_present, count_missing, mean and median after dropping null values only. Zero is observed.',{'values':[0,None,4,8,None,12,16,20]},{'count_present':6,'count_missing':2,'mean':10,'median':10})
    add('17-left-join','data','Left join orders to customers by customer_id, preserving order. Missing customer names must be null. Return rows containing order_id,name,amount.',{'customers':[{'customer_id':1,'name':'Ada'},{'customer_id':2,'name':'Lin'}],'orders':[{'order_id':'x','customer_id':2,'amount':10},{'order_id':'y','customer_id':3,'amount':7},{'order_id':'z','customer_id':1,'amount':5}]},{'rows':[{'order_id':'x','name':'Lin','amount':10},{'order_id':'y','name':None,'amount':7},{'order_id':'z','name':'Ada','amount':5}]})
    add('18-daily-aggregation','data','Timestamps carry explicit UTC offsets. Convert to UTC calendar dates and return totals by UTC YYYY-MM-DD.',{'events':[{'time':'2025-01-01T23:30:00-02:00','value':3},{'time':'2025-01-02T01:00:00+00:00','value':5},{'time':'2025-01-02T00:30:00+02:00','value':7}]},{'totals':{'2025-01-01':7,'2025-01-02':8}})
    add('19-unicode-file-count','files','Read text.txt as UTF-8. Return integer COUNTS: lines is the number of splitlines() entries; words is the number of whitespace-separated tokens; code_points is the number of Unicode code points including newline characters. Do not return arrays of lines or words.',{}, {'lines':3,'words':7,'code_points':len('Café forest\n研究 trees 🌲\nnaïve science\n')},{'text.txt':'Café forest\n研究 trees 🌲\nnaïve science\n'})
    log='INFO start\nWARN retry\nERROR disk\nINFO complete\nERROR timeout\nWARN retry\n'
    add('20-log-analysis','files','Read application.log. Return levels (counts), error_messages in occurrence order and duplicate_lines sorted lexicographically.',{}, {'levels':{'INFO':2,'WARN':2,'ERROR':2},'error_messages':['disk','timeout'],'duplicate_lines':['WARN retry']},{'application.log':log})
    add('21-json-lines','files','Parse all nonempty lines of records.jsonl. Return record_count, total_amount and ids with status=ok in input order.',{}, {'record_count':4,'total_amount':20,'ok_ids':['a','d']},{'records.jsonl':'{"id":"a","amount":3,"status":"ok"}\n{"id":"b","amount":7,"status":"failed"}\n\n{"id":"c","amount":8,"status":"pending"}\n{"id":"d","amount":2,"status":"ok"}\n'})
    add('22-csv-quoting','files','Read quoted.csv using CSV parsing. Return records with name,note (strings) and amount (integer), preserving quoted commas/newlines.',{}, {'records':[{'name':'Ada, A.','note':'line one\nline two','amount':4},{'name':'Lin','note':'He said "yes"','amount':9}]},{'quoted.csv':'name,note,amount\n"Ada, A.","line one\nline two",4\nLin,"He said ""yes""",9\n'})
    add('23-config-diff','files','Compare before.json and after.json at the top level. Return added and removed key names sorted, and changed mapping common modified keys to [before,after].',{}, {'added':['batch'],'removed':['legacy'],'changed':{'rate':[.1,.05]}},{'before.json':'{"rate":0.1,"seed":7,"legacy":true}','after.json':'{"rate":0.05,"seed":7,"batch":32}'})
    add('24-markdown-links','files','Extract inline Markdown links from notes.md. Return links as label,url records in occurrence order, excluding images.',{}, {'links':[{'label':'Python','url':'https://docs.python.org/3/'},{'label':'Local','url':'results/table.csv'}]},{'notes.md':'# Notes\n[Python](https://docs.python.org/3/) and [Local](results/table.csv).\n![Figure](plot.png)\n'})
    # Real saved execution outputs, supplied without precomputed answers.
    fixture=Path(__file__).parent/'fixtures'
    provenance=json.loads((fixture/'provenance.json').read_text())
    def values(name,metric,selected=None):
        rows=list(csv.DictReader(io.StringIO((fixture/(name+'.csv')).read_text())))
        out=[]
        for r in rows:
            y=int(float(r['label'])); probs=[float(r['p'+str(i)]) for i in range(10)]
            if selected is not None and y!=selected: continue
            if metric=='accuracy': value=float(max(range(10),key=probs.__getitem__)==y)
            elif metric=='log_loss': value=-math.log(probs[y])
            elif metric=='brier': value=sum((p-int(i==y))**2 for i,p in enumerate(probs))
            elif metric=='confidence': value=max(probs)
            out.append(value)
        return out
    writing=[
        ('25-paired-accuracy','short18','short19','accuracy','higher',None),
        ('26-class-eight-errors','short18','short19','accuracy','higher',8),
        ('27-paired-logloss','short18','short19','log_loss','lower',None),
        ('28-confidence-summary','short18','short19','confidence','higher',None),
        ('29-multiclass-brier','short18','short19','brier','lower',None),
        ('30-compute-budget-confound','short18','long17','accuracy','higher',None),
    ]
    for ident,baseline,candidate,metric,direction,label in writing:
        bv=values(baseline,metric,label); cv=values(candidate,metric,label)
        b=statistics.mean(bv); c=statistics.mean(cv); gain=b-c if direction=='lower' else c-b
        data={'baseline_file':baseline+'.csv','candidate_file':candidate+'.csv','metric':metric,'direction':direction,'label_filter':label,'paired_unit':'observation_id'}
        expected={'baseline_mean':b,'candidate_mean':c,'improvement':gain,'n_pairs':len(bv),'better_method':'candidate' if gain>1e-12 else 'baseline' if gain< -1e-12 else 'tie','source_id':'qualification-digits','data_file':'input.json','unit':'observation_id','actual_saved_outputs':True}
        files={'provenance.json':json.dumps(provenance),baseline+'.csv':(fixture/(baseline+'.csv')).read_text(),candidate+'.csv':(fixture/(candidate+'.csv')).read_text()}
        formulas='Accuracy is mean(argmax probability == label); log_loss uses natural log of true-label probability; brier is mean of SUM of squared multiclass errors (do not divide by ten); confidence is mean maximum probability and does not itself measure accuracy.'
        add(ident, 'writing',
            'Recompute the requested metric from the actual saved prediction CSVs, applying label_filter only when non-null. '
            'Pair observations by observation_id. ' + formulas +
            ' Write an English report.md of 80–180 words. Label the decimal values directly as baseline mean, candidate mean, '
            'and signed improvement, using forms such as "baseline mean: <value>". Positive improvement means candidate has '
            'the preferred metric. State the better method or tie and number of paired observations. Read provenance.json. '
            'State the seed/regularization confound and compute budget scope; a confidence increase is not necessarily a quality improvement. '
            'Use citations [data:input.json] and [source:qualification-digits]. result.json must contain '
            'baseline_mean,candidate_mean,improvement,n_pairs,better_method,source_id,data_file,unit,actual_saved_outputs. '
            'Metadata definitions: data_file is the task-manifest filename "input.json"; source_id is provenance.json source_id '
            '("qualification-digits"); unit is input.json paired_unit ("observation_id"). These identify supplied inputs, not computed metric values. '
            'No significance, causal, novelty or generalization claim is supported. These are actual prior run outputs, not simulated results.',
            data, expected, files, extra=('report.md',))
    return tasks


def compare(expected, observed, path='result'):
    failures=[]
    if isinstance(expected,dict):
        if not isinstance(observed,dict): return [f'{path}: expected an object']
        for key,value in expected.items():
            if key not in observed: failures.append(f'{path}.{key}: missing')
            else: failures.extend(compare(value,observed[key],path+'.'+key))
    elif isinstance(expected,list):
        if not isinstance(observed,list) or len(expected)!=len(observed): return [f'{path}: wrong list length/type']
        for index,(a,b) in enumerate(zip(expected,observed)): failures.extend(compare(a,b,f'{path}[{index}]'))
    elif isinstance(expected,(int,float)) and not isinstance(expected,bool):
        numeric=isinstance(observed,(int,float)) and not isinstance(observed,bool)
        equal=numeric and math.isfinite(observed) and (expected==observed if isinstance(expected,int) else math.isclose(expected,observed,rel_tol=1e-8,abs_tol=1e-10))
        if not equal: failures.append(f'{path}: expected {expected!r}, observed {observed!r}')
    elif type(expected)!=type(observed) or expected!=observed:
        failures.append(f'{path}: expected {expected!r}, observed {observed!r}')
    return failures


def check(task, artifacts, *, oracle_revision=ORACLE_REVISION):
    failures=[]
    for path in task.outputs:
        if not artifacts.get(path): failures.append(f'Missing/empty artifact: {path}')
    try: observed=json.loads(artifacts.get('result.json',''))
    except (ValueError,TypeError): return failures+['result.json is not valid JSON']
    failures.extend(compare(task.expected,observed))
    if task.category=='writing':
        prose=artifacts.get('report.md',''); words=re.findall(r"\b[\w'-]+\b",prose)
        if not 80<=len(words)<=180: failures.append(f'report.md word count {len(words)} is outside 80–180')
        for marker in ('[data:input.json]','[source:qualification-digits]'):
            if marker not in prose: failures.append(f'report.md missing source binding {marker}')
        for phrase in ('observation','seed','budget'):
            if phrase not in prose.lower(): failures.append(f'report.md omits required scope term {phrase}')
        # Earlier in-flight attempts use the rubric saved when they started.
        legacy_numbers = None
        if oracle_revision < 3:
            legacy_numbers = re.findall(r'(?<![\w])[+-]?\d+(?:\.\d+)?', prose)
        metric_text = prose.replace('*', '').replace('`', '').replace('−', '-')
        number = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?'
        for field in ('baseline_mean', 'candidate_mean', 'improvement'):
            value = task.expected[field]
            if legacy_numbers is None:
                label = field.replace('_', r'[ _]+')
                claims = re.findall(
                    rf'\b{label}\b\s*(?:[:=|]|\b(?:is|was)\b)\s*({number})(?![\w]|\.\d)'
                    r'(?:\s*(%|percent(?:age)?\b))?', metric_text, re.I,
                )
                bound = bool(claims) and all(
                    not unit and math.isclose(value, float(n), rel_tol=0, abs_tol=0.00051)
                    for n, unit in claims
                )
            else:
                bound = any(math.isclose(value, float(n), rel_tol=0, abs_tol=0.00051) for n in legacy_numbers)
            if not bound:
                failures.append(f'report.md does not bind numeric {field}={value:g}')
    if task.id=='01-prime-independence':
        limit=json.loads(task.inputs['input.json'])['limit']
        expected=[n for n in range(2,limit+1) if all(n%d for d in range(2,math.isqrt(n)+1))]
        for name in ('sieve_primes.json','trial_primes.json'):
            try: failures.extend(compare(expected,json.loads(artifacts.get(name,'')),name))
            except ValueError: failures.append(f'{name}: invalid JSON')
        # Output agreement is checked here; algorithm independence also requires
        # inspecting the two preserved implementation artifacts.
    return failures
