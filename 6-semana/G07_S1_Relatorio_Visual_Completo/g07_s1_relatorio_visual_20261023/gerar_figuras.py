#!/usr/bin/env python3
"""Regenera figuras em PNG do relatório G07-S1, a partir de dados/metricas_g07_s1.json.
Requer matplotlib e numpy. Apenas visualização dos dados transcritos; não executa simulações.
"""
from pathlib import Path
import json, math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import LogNorm
from matplotlib.patches import FancyBboxPatch, Polygon
from matplotlib.ticker import MaxNLocator

ROOT=Path(__file__).resolve().parent
D=json.loads((ROOT/'dados/metricas_g07_s1.json').read_text(encoding='utf-8'))
DIR=ROOT/'figuras'; DIR.mkdir(exist_ok=True)
C0='#cf703a'   # original36
C1='#178f8d'   # dirigido52
GT='#26344c'
SOFT='#edf2f5'
BG='#ffffff'
plt.rcParams.update({'figure.facecolor':BG, 'axes.facecolor':BG,'font.family':'DejaVu Sans',
                     'axes.spines.top':False, 'axes.spines.right':False,'font.size':10,
                     'axes.titleweight':'bold','savefig.facecolor':BG})

def save(fig,name):
    fig.savefig(DIR/name,dpi=180,bbox_inches='tight',pad_inches=.24)
    plt.close(fig)

def axis(ax, ylabel=None, ylim=None):
    if ylabel:ax.set_ylabel(ylabel)
    if ylim:ax.set_ylim(*ylim)
    ax.grid(axis='y',alpha=.17,zorder=0)
    ax.set_axisbelow(True)

def mini_label(ax,x,y,text,**kwargs):
    ax.annotate(text,(x,y),ha='center',va='bottom',fontsize=9,xytext=(0,3),textcoords='offset points',**kwargs)

poly=np.array(D['cenario']['vertices_gt_xy_m'],float)
rectangle=np.array([[0,0],[6,0],[6,5],[0,5]],float)

def draw_room(ax,outline=poly,show_wall_names=False,fill_color='#e5f3f1',show_dims=False):
    ax.add_patch(Polygon(outline,closed=True,facecolor=fill_color,edgecolor=GT,lw=2.3,joinstyle='miter'))
    ax.set_aspect('equal'); ax.set_xlim(-.55,6.45); ax.set_ylim(-.60,5.6)
    ax.set_xticks([0,1,2,3,4,5,6]);ax.set_yticks([0,1,2,3,4,5]);ax.grid(alpha=.12)
    ax.set_xlabel('x (m)');ax.set_ylabel('y (m)')
    if show_wall_names:
        for i,p in enumerate(poly):
            q=poly[(i+1)%len(poly)];mid=(p+q)/2
            v=np.array([-.07,.11]) if i in [3,4,5] else np.array([.0,.0])
            ax.text(*(mid+v),f'{i:02d}',ha='center',va='center',weight='bold',fontsize=8,
                    bbox=dict(boxstyle='round,pad=.22',facecolor='white',edgecolor='none',alpha=.85))
    if show_dims:
        ax.annotate('6,0 m',(3,-.30),ha='center',fontsize=11,color=GT)
        ax.annotate('5,0 m',(-.32,2.4),ha='center',va='center',rotation=90,fontsize=11,color=GT)
        ax.annotate('rebaixo: 1,6 × 0,7 m',(3,4.44),ha='center',fontsize=8.5,color=GT)

# 01 workflow
fig,ax=plt.subplots(figsize=(15,3.1));ax.set_xlim(0,16);ax.set_ylim(-.45,2.4);ax.axis('off')
stages=[('Blender','Cena S1 e RGB'),('COLMAP','SfM cego'),('G07-E1','Malha Manhattan'),('G07-F','Geometria × GT'),('G07-G1','Caminhos 0/1'),('G07-G2–G7','RIR e auditorias')]
xpos=[.12,2.80,5.48,8.16,10.84,13.52]
for i,((title,subtitle),x) in enumerate(zip(stages,xpos)):
    col=GT if i<3 else (C1 if i>=4 else '#5e7290')
    patch=FancyBboxPatch((x,0.55),2.35,1.22,boxstyle='round,pad=0.10,rounding_size=0.13',
                         linewidth=0,facecolor=col)
    ax.add_patch(patch)
    ax.text(x+1.175,1.28,title,ha='center',va='center',color='white',weight='bold',fontsize=12)
    ax.text(x+1.175,.91,subtitle,ha='center',va='center',color='white',fontsize=9)
    if i<5: ax.annotate('',xy=(x+2.64,1.16),xytext=(x+2.46,1.16),arrowprops={'arrowstyle':'->','lw':2,'color':'#84919a'})
ax.text(8.0,.05,'GT usado no alinhamento e na avaliação pós-hoc; não fornece escala autônoma ao SfM.',ha='center',fontsize=9,style='italic',color='#58677a')
save(fig,'01_pipeline_g07.png')

# 02 topological schematic
fig,axs=plt.subplots(1,3,figsize=(15.8,5.2))
for ax,outline,title,col in zip(axs,[poly,rectangle,poly],['GT: Blender / S1','Original36: contorno retangular','Dirigido52: contorno não convexo'],[GT,C0,C1]):
    draw_room(ax,outline=outline,fill_color=(SOFT if col==GT else ('#fcf0e8' if col==C0 else '#ddf3ef')))
    ax.set_title(title,fontsize=12,pad=12,color=col)
axs[0].annotate('rebaixo 0,7 m',xy=(3,4.3),xytext=(2.6,3.55),arrowprops={'arrowstyle':'->'},fontsize=9)
axs[1].text(3,4.65,'Detalhe omitido',ha='center',color=C0,weight='bold')
axs[2].text(3,3.75,'Detalhe recuperado',ha='center',color=C1,weight='bold')
fig.suptitle('Esquema topológico (não é renderização nem coordenadas OBJ medidas)',fontsize=14,fontweight='bold',y=1.025)
save(fig,'02_plantas_topologicas_esquematicas.png')

# 03 coverage bars
fig,ax=plt.subplots(figsize=(10,4.0))
keys=list(D['cobertura_superficial_pct']); dat=np.array([D['cobertura_superficial_pct'][k] for k in keys]); x=np.arange(len(keys));w=.28
b0=ax.bar(x-w/2,dat[:,0],w,color=C0,label='Original36',zorder=3)
b1=ax.bar(x+w/2,dat[:,1],w,color=C1,label='Dirigido52',zorder=3)
ax.set_xticks(x,keys);axis(ax,'Cobertura de células da parede (%)',(0,100));ax.legend(frameon=False,ncol=2,loc='upper center')
for bars in [b0,b1]:
    for b in bars: mini_label(ax,b.get_x()+b.get_width()/2,b.get_height(),f'{b.get_height():.1f}%')
ax.set_title('G07-D4 · Cobertura SfM nos laterais do rebaixo')
ax.text(.50,-.18,'Células de 20 cm; pontos com filtros de qualidade definidos em G07-D4.',transform=ax.transAxes,ha='center',fontsize=9,color='#65778a')
save(fig,'03_cobertura_paredes_criticas.png')

# 04 wall heatmap 2d finite segments
fig,axs=plt.subplots(1,2,figsize=(15,5.7))
values=np.array(list(D['paredes_gt_erro_medio_mm'].values()))
norm=LogNorm(vmin=.2,vmax=550)
for idx,ax in enumerate(axs):
    draw_room(ax,outline=poly,show_wall_names=True,fill_color='#f4f7f8')
    # line overlays by per-wall mean error
    segments=[[poly[i],poly[(i+1)%len(poly)]] for i in range(8)]
    lc=LineCollection(segments,cmap='magma',norm=norm,linewidths=10,capstyle='projecting',zorder=5)
    lc.set_array(values[:,idx]);ax.add_collection(lc)
    for j in [3,4,5]:
        mid=(poly[j]+poly[(j+1)%8])/2
        if idx==0:ax.text(mid[0],mid[1]-.22,f'{values[j,idx]:.0f} mm',fontsize=9,ha='center',color='#8f1c2d',weight='bold')
    ax.set_title(['Original36: paredes ausentes no rebaixo','Dirigido52: paredes recuperadas'][idx],fontsize=12)
cbar=fig.colorbar(lc,ax=axs.tolist(),fraction=.023,pad=.025)
cbar.set_label('Distância média GT → malha, por parede (mm), escala log')
fig.suptitle('G07-F · Mapa espacial de divergências nas paredes GT',fontsize=15,fontweight='bold',y=1.005)
save(fig,'04_mapa_divergencia_paredes_gt.png')

# 05 geometry metrics charts separate panels
fig,axs=plt.subplots(1,3,figsize=(15,4))
mods=['Original36','Dirigido52'];ix=np.arange(2)
vals=[[D['geometria'][k][z] for k in ['original36','dirigido52']] for z in ['iou_planta','erro_volume_pct','gt_obj_p95_mm']]
labels=['IoU de planta','Erro de volume','GT → malha: P95']
units=['','%','mm']
for ax,yy,label,unit in zip(axs,vals,labels,units):
    ba=ax.bar(ix,yy,color=[C0,C1],width=.56,zorder=3)
    ax.axhline(0,color='#76808a',lw=.7)
    ax.set_xticks(ix,mods);ax.set_title(label)
    if unit:ax.set_ylabel(unit)
    axis(ax)
    for b,v in zip(ba,yy):
        ax.annotate(f'{v:.4f}' if label=='IoU de planta' else f'{v:,.2f}',
                    (b.get_x()+b.get_width()/2,b.get_height()),ha='center',
                    xytext=(0,5 if v>=0 else -17),textcoords='offset points',fontsize=10,weight='bold')
    if label=='IoU de planta':ax.set_ylim(0.94,1.01)
fig.suptitle('G07-F · Fidelidade geométrica pós-alinhamento Sim3 por GT',fontsize=14,fontweight='bold',y=1.03)
save(fig,'05_metricas_geometricas.png')

# 06 ray paths comparative summary
fig,axs=plt.subplots(1,3,figsize=(15,4))
metrics=[('direto_espurio','Raios diretos falsos'),('reflexoes_omitidas','Reflexões GT omitidas'),('reflexoes_espurias','Reflexões espúrias')]
for ax,(name,title) in zip(axs,metrics):
    yy=[D['trajetorias_G1'][k][name] for k in ['original36','dirigido52']]
    bars=ax.bar([0,1],yy,color=[C0,C1],width=.56)
    for b,v in zip(bars,yy):mini_label(ax,b.get_x()+b.get_width()/2,v,str(v))
    ax.set_xticks([0,1],['Original36','Dirigido52']);axis(ax,'Quantidade',(0,max(yy)*1.23+1));ax.set_title(title)
fig.suptitle('G07-G1 · Erros de existência de trajetórias em 87 pares fonte–receptor',fontsize=14,fontweight='bold',y=1.03)
save(fig,'06_erros_trajetorias.png')

# 07 order1-3 energy values mean P95; grouped lines
fig,axs=plt.subplots(1,2,figsize=(14.5,4.9))
orders=['1','2','3']; r=D['RIR_G3']['por_ordem'];
for ax,kind,title in zip(axs,['energia_media_abs_db','energia_p95_abs_db'],['Erro absoluto médio de energia','Erro absoluto P95 de energia']):
    for idx,label,col in [(0,'Original36',C0),(1,'Dirigido52',C1)]:
        vals=[r[o][kind][idx] for o in orders]
        ax.plot([1,2,3],vals,marker='o',ms=8,lw=2.5,color=col,label=label)
        for x,y in zip([1,2,3],vals):ax.annotate(f'{y:.3f}',(x,y),xytext=(0,8),textcoords='offset points',ha='center',fontsize=9,color=col)
    ax.set_xlim(.7,3.3);ax.set_xticks([1,2,3]);axis(ax,'dB');ax.set_xlabel('Ordem máxima ISM');ax.set_title(title)
axs[0].legend(frameon=False)
fig.suptitle('G07-G3 · Diferenças de energia em RIR acumuladas até cada ordem',fontsize=14,fontweight='bold',y=1.03)
save(fig,'07_energia_rir_por_ordem.png')

# 08 waveform/envelope means
fig,axs=plt.subplots(1,2,figsize=(14.5,4.8))
for ax,key,title in zip(axs,['rir_l2_media','envoltoria_l2_media'],['L2 relativo médio — forma de onda','L2 relativo médio — envoltória']):
    for idx,label,col in [(0,'Original36',C0),(1,'Dirigido52',C1)]:
        vals=[r[o][key][idx] for o in orders]
        ax.plot([1,2,3],vals,marker='o',ms=8,lw=2.5,color=col,label=label)
        for x,y in zip([1,2,3],vals):ax.annotate(f'{y:.3f}',(x,y),xytext=(0,9),textcoords='offset points',ha='center',fontsize=9,color=col)
    ax.set_xlim(.7,3.3);ax.set_xticks([1,2,3]);axis(ax,'Erro relativo (adimensional)');ax.set_xlabel('Ordem máxima ISM');ax.set_title(title)
axs[0].legend(frameon=False)
fig.suptitle('G07-G3 · Erro médio da RIR e da envoltória energética',fontsize=14,fontweight='bold',y=1.03)
save(fig,'08_erros_l2_rir_por_ordem.png')

# 09 spatial map mic/source and hotspot
fig,ax=plt.subplots(figsize=(10,7))
draw_room(ax,outline=poly,show_dims=True,fill_color='#eef7f6')
xs=D['cenario']['receptores_grid_x_m'];ys=D['cenario']['receptores_grid_y_m']
xx,yy=np.meshgrid(xs,ys)
ax.scatter(xx.ravel(), yy.ravel(),s=48,marker='o',facecolor='#cad4de',edgecolor='#8494a7',label='25 receptores da grade',zorder=6)
for rx,ry in D['cenario']['receptores_adicionais_xy_m']:
    ax.scatter(rx,ry,s=45,marker='D',facecolor='#b2c0df',edgecolor='#627493',zorder=7)
for name,(x,y,z) in D['cenario']['fontes_m'].items():
    ax.scatter(x,y,marker='*',s=250,color=C0 if name=='S_center' else GT,edgecolor='white',linewidth=1.2,zorder=9)
    if name=='S_center':
        ax.annotate('S_center\n(3,0; 2,0; 1,5)',(x,y),xytext=(12,13),textcoords='offset points',fontsize=10,color=GT,weight='bold')
    elif name=='S_left_wing':ax.annotate('S_left_wing',(x,y),xytext=(-10,-22),textcoords='offset points',ha='center',fontsize=9,color=GT)
    else:ax.annotate('S_right_wing',(x,y),xytext=(-10,-22),textcoords='offset points',ha='center',fontsize=9,color=GT)
ax.scatter(3,.8,s=330,facecolor='none',edgecolor='#d34c39',linewidths=2.6,zorder=12)
ax.annotate('Receptor singular\nR_grid_2_0',(3,.8),xytext=(17,19),textcoords='offset points',color='#aa3829',fontsize=10,weight='bold')
ax.plot([3,3],[.8,2],ls='--',color='#c14a35',lw=1.5,alpha=.75)
ax.set_title('S1 · Fontes, receptores e caso de simetria G07-G5/G6',fontsize=13,pad=12)
ax.text(.5,-.14,'Vista superior; as alturas z não estão representadas.',transform=ax.transAxes,ha='center',fontsize=9,color='#65778a')
save(fig,'09_fontes_receptores_hotspot.png')

# 10 sensitivity count and dB
fig,axs=plt.subplots(1,2,figsize=(14.5,4.75))
lab=['central','x −5 mm','x +5 mm','z −5 mm','z +5 mm']
y2=[29,25,25,25,25];y3=[71,61,61,61,61]
x=np.arange(len(lab));w=.34
axs[0].bar(x-w/2,y2,w,color='#6e89ac',label='ISM até ordem 2');axs[0].bar(x+w/2,y3,w,color=GT,label='ISM até ordem 3')
axs[0].set_xticks(x,lab,rotation=20,ha='right');axis(axs[0],'Imagens visíveis acumuladas');axs[0].legend(frameon=False);axs[0].set_title('Fontes imagem visíveis (GT)')
vals=[0,D['hotspot']['GT_mudanca_energia_db_ordem3']['x-5'],D['hotspot']['GT_mudanca_energia_db_ordem3']['x+5'],D['hotspot']['GT_mudanca_energia_db_ordem3']['z-5'],D['hotspot']['GT_mudanca_energia_db_ordem3']['z+5']]
axs[1].bar(x,vals,color=[GT]+[C1]*4,width=.6)
axs[1].set_xticks(x,lab,rotation=20,ha='right');axis(axs[1],'Δ energia GT relativo ao centro (dB)',(-3.35,.3));axs[1].set_title('Energia GT: ISM até ordem 3')
fig.suptitle('G07-G5 · Sensibilidade de ±5 mm do receptor singular',fontsize=14,fontweight='bold',y=1.03)
save(fig,'10_sensibilidade_gt_visibilidade.png')

# 11 local robustness relative l2 order3 paired xz vs y
fig,axs=plt.subplots(1,2,figsize=(14.6,4.7))
for ax,key,ttl in zip(axs,['rir_l2_media','env_l2_media'],['L2 relativo de RIR · ordem 3','L2 relativo da envoltória · ordem 3']):
    vals=np.array([D['G7']['ordem_3_xz'][key],D['G7']['ordem_3_y'][key]])
    x=np.arange(2);w=.28
    ax.bar(x-w/2,vals[:,0],w,color=C0,label='Original36',zorder=3)
    ax.bar(x+w/2,vals[:,1],w,color=C1,label='Dirigido52',zorder=3)
    ax.set_xticks(x,['X/Z ±5/10 mm\n(n = 8)','Y ±5/10 mm\n(n = 4)'])
    axis(ax,'Erro relativo (adimensional)',(0,vals.max()*1.3));ax.set_title(ttl)
    for j in range(2):
        mini_label(ax,x[j]-w/2,vals[j,0],f'{vals[j,0]:.3f}')
        mini_label(ax,x[j]+w/2,vals[j,1],f'{vals[j,1]:.3f}')
axs[0].legend(frameon=False)
fig.suptitle('G07-G7 · Comparação pareada sob deslocamentos locais',fontsize=14,fontweight='bold',y=1.02)
save(fig,'11_robustez_perturbacoes.png')

# 12 experimental control H1 schematic
fig,ax=plt.subplots(figsize=(13,4.0));ax.axis('off');ax.set_xlim(0,13);ax.set_ylim(0,4)
cards=[('Original36','36 RGB originais',GT),('Geral52','36 originais + 16 gerais','#587ea2'),('Dirigido52','36 originais + 16 dirigidas',C1)]
for i,(name,txt,col) in enumerate(cards):
    x=.3+4.2*i
    ax.add_patch(FancyBboxPatch((x,1.2),3.6,1.8,boxstyle='round,pad=.1',facecolor=col,edgecolor='none'))
    ax.text(x+1.8,2.4,name,ha='center',color='white',weight='bold',fontsize=15)
    ax.text(x+1.8,1.80,txt,ha='center',color='white',fontsize=9)
ax.text(6.6,3.56,'H1 · Controle experimental planejado — NÃO EXECUTADO',ha='center',fontsize=14,weight='bold',color=GT)
ax.annotate('Mesmas 16 posições\npara Geral52 e Dirigido52',xy=(10.0,1.09),xytext=(6.55,.15),ha='center',fontsize=10,color='#58677a',arrowprops={'arrowstyle':'->','color':'#58677a'})
save(fig,'12_controle_orientacao_h1_planejado.png')

print('Figuras geradas:')
for f in sorted(DIR.glob('*.png')):print(f.name,round(f.stat().st_size/1024),'KiB')
