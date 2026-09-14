"""前後端評分引擎交叉驗證：Python 產生案例與答案，前端 scripts/xval.js 算 JS 答案，這裡比對。"""
import json, random, sys, os, subprocess, math
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT,'backend'))
import engine as E
random.seed(2026)
cases={}
d=[]
for _ in range(300):
    n=random.choice([8,20,30]); base=random.uniform(50,5000); drift=random.uniform(-0.03,0.03)
    d.append({'oi':[base*(1+drift*i+random.uniform(-0.01,0.01)) for i in range(n)],
      'ls':[random.uniform(0.5,2.5) for _ in range(n)],'taker':[random.uniform(0.6,1.6) for _ in range(n)],
      'funding':random.choice([None,random.uniform(-0.004,0.004)]),
      'fundingHist':[random.uniform(-0.003,0.003) for _ in range(random.choice([0,4,20]))],
      'chg24':random.choice([None,random.uniform(-15,15)])})
cases['deriv']=d
stages=['ignite','accel','hot','dump','fade','quiet','active','unknown',None]
quads=['多方新倉','空單回補','空方新倉','多單出場','盤整',None]
g=[]
for _ in range(400):
    q=random.choice(quads)
    g.append([{'score':random.choice([None,round(random.uniform(20,95),2)]),'stage':random.choice(stages),'rvol7':random.choice([None,round(random.uniform(0.5,5),2)])},
              None if q is None else {'quadrant':q,'bias':random.choice([-1,-0.3,0,0.3,1]),'crowd':round(random.uniform(0,100),1),'fuel':round(random.uniform(-100,100),1)},
              random.choice([True,False])])
cases['gate']=g
st=[]
for _ in range(300):
    p=random.uniform(0.01,100)
    st.append([{'price':p,'ma60':random.choice([None,p*random.uniform(0.7,1.05)]),'stop':random.choice([None,p*random.uniform(0.98,1.3)]),'atr':random.choice([None,p*random.uniform(0.005,0.15)])},
               random.choice([True,False]),random.choice(['ma','atr','tighter']),random.choice([1,1.5,2]),random.choice([8,12,15]),1.5])
cases['stop']=st
py={}
py['deriv']=[]
for x in d:
    a=E.deriv_analyze(x); a['_bull']=E.deriv_score_adjust(70,a,'bull')[0]; a['_bear']=E.deriv_score_adjust(70,a,'bear')[0]; py['deriv'].append(a)
py['gate']=[list(E.trade_gate(a,b,c)) for a,b,c in g]
py['stop']=[]
for c in st:
    p,dd=E.stop_pct(*c); py['stop'].append([p,dd['used']])
for k,v in cases.items(): json.dump(v,open(f'/tmp/xv_{k}.json','w'))
def cmp(a,b):
    if isinstance(a,dict) and isinstance(b,dict): return all(cmp(a.get(k),b.get(k)) for k in set(a)|set(b))
    if isinstance(a,list) and isinstance(b,list): return len(a)==len(b) and all(cmp(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)) and not isinstance(a,bool): return abs(a-b)<1e-9
    return a==b
bad_total=0
for k in ('deriv','gate','stop'):
    subprocess.run(['node','scripts/xval.js',k],cwd=os.path.join(ROOT,'frontend'),check=True,capture_output=True)
    js=json.load(open(f'/tmp/xv_{k}_js.json'))
    if k=='gate': js=[[x['blocks'],x['warns']] for x in js]
    bad=sum(1 for a,b in zip(js,py[k]) if not cmp(a,b)); bad_total+=bad
    print(f'  {k:<6} {len(js)} 筆　不一致 {bad} 筆')
if bad_total: sys.exit(1)
print('  ✓ 前後端引擎完全一致')
