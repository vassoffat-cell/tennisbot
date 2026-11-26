"""26.11 (решение пользователя: «параллельные матчи не должны влиять на успех»): связаны ли итоги одновременных матчей.
ρ — корреляция стандартизованных остатков z = (W − P)/√(P(1−P)) по парам разных матчей, начало ближе 2 ч, против пар тех же суток.
① история футбола (matches.csv.gz, Pinnacle без маржи, лучшая линия P ≥ 0,60 на матч); ② живые теневые портфели всех видов (⭐-зона).
Независимы → ρ ≈ 0. Запуск: python3 engine/research/parallel_corr.py  (аудит 0ж⑤ — еженедельно)."""
import warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
print("① история футбола")
import pandas as pd, numpy as np
d=pd.read_csv(str(ROOT)+'/football/data/matches.csv.gz')
d=d.dropna(subset=['ps_h','ps_d','ps_a','hg','ag','time','date'])
def fair(a):                                   # пропорционально (для корреляции достаточно)
    q=1/np.asarray(a,float); return q/q.sum(1,keepdims=True)
P=fair(d[['ps_h','ps_d','ps_a']].values)
res=np.select([d.hg>d.ag,d.hg==d.ag],[0,1],2)
cand_p=[P[:,0],P[:,2]]; cand_w=[(res==0),(res==2)]
has_t=d[['ps_o25','ps_u25']].notna().all(axis=1).values
PT=np.where(has_t[:,None], fair(d[['ps_o25','ps_u25']].fillna(2).values), np.nan)
tot=(d.hg+d.ag).values
cand_p+= [PT[:,0],PT[:,1]]; cand_w+=[tot>2.5,tot<2.5]
Pm=np.vstack(cand_p).T; Wm=np.vstack(cand_w).T.astype(float)
Pm=np.where(np.isnan(Pm),-1,Pm)
j=Pm.argmax(1); p=Pm[np.arange(len(d)),j]; w=Wm[np.arange(len(d)),j]
m=p>=0.60
d=d[m].copy(); d['p']=p[m]; d['w']=w[m]
d['t']=pd.to_datetime(d.date+' '+d.time)
d['z']=(d.w-d.p)/np.sqrt(d.p*(1-d.p))
print(f'матчей с ⭐-подобной линией P≥0,60: {len(d)}, угадано {d.w.mean():.3f} при обещанных {d.p.mean():.3f}')
d=d.sort_values('t'); day=d.t.dt.floor('D').values
t=(d.t.values.astype('datetime64[m]').astype(np.int64)); z=d.z.values; lg=d['div'].values
acc={k:[0.0,0] for k in ('sim','sim_same','sim_diff','far')}
perday=[]
for dd in np.unique(day):
    ii=np.where(day==dd)[0]
    if len(ii)<2: continue
    a,b=np.triu_indices(len(ii),1); A,B=ii[a],ii[b]; pr=z[A]*z[B]; s=np.abs(t[A]-t[B])<120
    sl=lg[A]==lg[B]
    for k,mm in (('sim',s),('sim_same',s&sl),('sim_diff',s&~sl),('far',~s)):
        acc[k][0]+=pr[mm].sum(); acc[k][1]+=mm.sum()
    perday.append((pr[s].sum(), s.sum()))
for k,(sm,n) in acc.items(): print(f'{k}: пар {n}, ρ = {sm/n:+.4f}')
pdy=np.array(perday); rng=np.random.default_rng(7); bs=[]
for _ in range(2000):
    ix=rng.integers(0,len(pdy),len(pdy)); bs.append(pdy[ix,0].sum()/pdy[ix,1].sum())
print(f'ρ одновременных: 95% интервал [{np.percentile(bs,2.5):+.4f}; {np.percentile(bs,97.5):+.4f}] ({len(pdy)} сут.)')
# что значит ρ для 3 одновременных ⭐ по P 0,65: вероятность «все три мимо»
from itertools import product
for rho in (0.0, np.percentile(bs,97.5)):
    pp=0.65; q=1-pp; # корреляция бернулли: P(LL)=q²+ρ·pq; для трёх — приближение через гауссову копулу
    from scipy.stats import norm, multivariate_normal
    c=norm.ppf(q); cov=np.full((3,3),rho)+np.eye(3)*(1-rho)
    # ρ бернулли ≈ ρ латентной (оценка сверху для малых ρ)
    print(f'ρ={rho:+.3f}: P(все 3 мимо) = {multivariate_normal(mean=[0]*3,cov=cov).cdf([c]*3):.4f} (независимо {q**3:.4f})')
print("② живые теневые портфели (⭐-зона)")
R=str(ROOT)+'/'
fr=[]
for sp,f,idc in (('⚽','football/data/shadow/football_2026-11.csv','espn_id'),('🏒','hockey/data/shadow/hockey_2026-11.csv','id'),('🎾','data/shadow/tennis_2026-11.csv','espn_id')):
    d=pd.read_csv(R+f); d=d[d.result.isin(['W','L'])].copy()
    k=d['k_best'] if 'k_best' in d else d['k']; d['kk']=k.fillna(d['k'])
    d=d[(d.p>=0.60)&(d.kk>=1.35)&(d.ev>=-0.03)]
    d=d.sort_values('ev',ascending=False).drop_duplicates(idc)       # одна ⭐-подобная сторона на матч
    d['sp']=sp; d['mid']=sp+d[idc].astype(str); d['t']=pd.to_datetime(d.start_utc)
    fr.append(d[['sp','mid','t','p','result','match','label']])
d=pd.concat(fr).sort_values('t').reset_index(drop=True)
d['w']=(d.result=='W').astype(int); d['v']=d.p*(1-d.p); d['z']=(d.w-d.p)/np.sqrt(d.v)
print('матчей (⭐-зона, 1 сторона на матч):', len(d), d.sp.value_counts().to_dict(), 'угадано', d.w.sum(), 'обещано', round(d.p.sum(),1))
# пары матчей: одновременные (|Δ|<2ч) против остальных в тех же сутках
t=d.t.values.astype('datetime64[m]').astype(np.int64); z=d.z.values; day=d.t.dt.floor('D').values
I,J=np.triu_indices(len(d),1)
dt=np.abs(t[I]-t[J])/60; same=day[I]==day[J]
prod=z[I]*z[J]
for name,m in (('одновременные (< 2 ч)',dt<2),('те же сутки, ≥ 2 ч',(dt>=2)&same),('одновременно, разные виды спорта',(dt<2)&(d.sp.values[I]!=d.sp.values[J])),('одновременно, тот же вид',(dt<2)&(d.sp.values[I]==d.sp.values[J]))):
    x=prod[m]; print(f'{name}: пар {m.sum()}, корреляция ρ = {x.mean():+.3f}')
# бутстрап по суткам: ρ одновременных
days=np.unique(day); rng=np.random.default_rng(1); bs=[]
idx_by_day={dd:np.where(day==dd)[0] for dd in days}
for _ in range(2000):
    pick=rng.choice(days,len(days)); num=0;den=0
    for dd in pick:
        ii=idx_by_day[dd]
        if len(ii)<2: continue
        a,b=np.triu_indices(len(ii),1); A,B=ii[a],ii[b]; mm=np.abs(t[A]-t[B])<120
        num+=(z[A]*z[B])[mm].sum(); den+=mm.sum()
    bs.append(num/den if den else 0)
bs=np.array(bs); print(f'ρ одновременных: 95% интервал [{np.percentile(bs,2.5):+.3f}; {np.percentile(bs,97.5):+.3f}] (бутстрап по суткам, {len(days)} сут.)')
# волны: матчи, начинающиеся в пределах 2 ч друг от друга (цепочкой)
w=(d.t.diff()>pd.Timedelta(hours=2)).cumsum(); d['wave']=w
g=d.groupby('wave').agg(n=('w','size'),W=('w','sum'),P=('p','sum'),V=('v','sum'))
g2=g[g.n>=2]; S=(g2.W-g2.P)
print(f'волн с ≥2 матчами: {len(g2)}; дисперсия суммы / сумма дисперсий = {(S**2).sum()/g2.V.sum():.2f} (1,00 = независимы)')
# вероятность «все мимо» в волне: факт против независимой модели
allL=(g2.W==0).sum()
exp=sum(np.prod(1-d[d.wave==i].p.values) for i in g2.index)
print(f'волн «все мимо»: факт {allL}, ожидание при независимости {exp:.1f}')
