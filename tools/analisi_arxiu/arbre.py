import sqlite3, sys
c=sqlite3.connect('arxiu.db')
LEVEL=int(sys.argv[1]) if len(sys.argv)>1 else 1
LIM=int(sys.argv[2]) if len(sys.argv)>2 else 14
ONLY=sys.argv[3] if len(sys.argv)>3 else None
def pct_years(where, params):
    ys=[r[0] for r in c.execute(f'select myear from g where {where} and myear between 1995 and 2026 order by myear', params)]
    if not ys: return '-','-'
    return ys[len(ys)//10], ys[len(ys)*9//10]
for d, in c.execute('select drive_id from drives order by drive_id'):
    if ONLY and d!=ONLY: continue
    print('==', d)
    if LEVEL==1:
        rows=c.execute('select l1, count(*), sum(size)/1e9, sum(case when nd<=1 then size else 0 end)/1e9 from g where drive=? group by l1 order by sum(size) desc limit ?',(d,LIM)).fetchall()
        for l1,n,gb,gbu in rows:
            lay=c.execute('select layer, sum(size) from g where drive=? and l1=? group by layer order by sum(size) desc limit 1',(d,l1)).fetchone()
            y=pct_years('drive=? and l1=?',(d,l1))
            print('   %-40s %8d %8.1f %8.1f  %-7s %3d%%  %s-%s'%((l1 or '(arrel)')[:40],n,gb,gbu,lay[0],100*lay[1]/1e9/gb if gb else 0,y[0],y[1]))
    else:
        rows=c.execute('select l1,l2, count(*), sum(size)/1e9, sum(case when nd<=1 then size else 0 end)/1e9 from g where drive=? group by l1,l2 order by sum(size) desc limit ?',(d,LIM)).fetchall()
        for l1,l2,n,gb,gbu in rows:
            lay=c.execute('select layer, sum(size) from g where drive=? and l1=? and l2=? group by layer order by sum(size) desc limit 1',(d,l1,l2)).fetchone()
            y=pct_years('drive=? and l1=? and l2=?',(d,l1,l2))
            print('   %-52s %8d %8.1f %8.1f  %-7s %3d%%  %s-%s'%(((l1 or '(arrel)')+'\\'+(l2 or ''))[:52],n,gb,gbu,lay[0],100*lay[1]/1e9/gb if gb else 0,y[0],y[1]))
