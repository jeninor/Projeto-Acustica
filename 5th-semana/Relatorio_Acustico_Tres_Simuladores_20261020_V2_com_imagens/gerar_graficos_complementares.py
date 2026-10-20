#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduz gráficos de resposta R01–R25 e diferenças pareadas.

Dependências: numpy, pandas, matplotlib, pillow.
Execução: python gerar_graficos_complementares.py
Dados originais: arquivos CSV em dados/ (não modifica esses arquivos).
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent
FIG=ROOT/'figuras'
DATA=ROOT/'dados'
FIG.mkdir(exist_ok=True)
PAIR_DIR=FIG/'mapas_pareados_individuais'
PAIR_DIR.mkdir(exist_ok=True)
plt.rcParams.update({'font.size': 10,'axes.titlesize':12,'axes.labelsize':11,'legend.fontsize':10,
                     'figure.facecolor':'white','savefig.facecolor':'white', 'axes.spines.top':False,
                     'axes.spines.right':False,'figure.dpi':110,'savefig.dpi':180})
SIMS=('ODEON','I-Simpa','Pyroomacoustics')
PAIRS=((0,1),(0,2),(1,2))

omni=pd.read_csv(DATA/'omni_25_receptores_analise.csv')
cf2=pd.read_csv(DATA/'cf2_75_receptores_analise.csv')
omni=omni.sort_values('Receptor').reset_index(drop=True)
cf2=cf2.sort_values(['angle','receiver']).reset_index(drop=True)
assert len(omni)==25 and set(omni.Receptor)=={f'R{i:02d}' for i in range(1,26)}
assert len(cf2)==75 and set(cf2.angle)=={0,-8,-16}

omni_spec={
 'nivel_relativo':('Nível relativo centrado','dB',['G_ODEON_centrado','SPL_ISimpa_rel_centrado_dB','SPL_PRA50000_rel_centrado_dB']),
 'edt':('EDT','s',['EDT_ODEON_s','EDT_ISimpa_s','EDT_PRA50000_s']),
 't30':('T30 (RT30)','s',['T30_ODEON_s','T30_ISimpa_s','T30_PRA50000_s']),
 'c80':('C80','dB',['C80_ODEON_dB','C80_ISimpa_dB','C80_PRA50000_dB']),
}
cf2_spec={
 'edt':('EDT','s',['ODEON_EDT_s','ISimpa_EDT_s','Pyroom_EDT_s']),
 't30':('T30 (RT30)','s',['ODEON_T30_s','ISimpa_T30_s','Pyroom_T30_s']),
 'c80':('C80','dB',['ODEON_C80_dB','ISimpa_C80_dB','Pyroom_C80_dB']),
}

# Correspondência de posições receptores = ordem para todos casos
ref_omni=omni.set_index('Receptor')[['X_m','Y_m']]
for a in (0,-8,-16):
 c=cf2[cf2.angle==a].set_index('receiver')
 assert all(np.isclose(c.loc[r,'x'], ref_omni.loc[r,'X_m'],atol=.01) and
            np.isclose(c.loc[r,'y'],ref_omni.loc[r,'Y_m'],atol=.01) for r in ref_omni.index)

# Escala simétrica global por métrica: permite comparar as diferenças entre
# os pares e também entre as quatro condições experimentais.
LIMITS={}
for key in ('edt','t30','c80'):
 vals=[]
 for frame, spec in [(omni,omni_spec)]+[(cf2[cf2.angle==a],cf2_spec) for a in (0,-8,-16)]:
  cols=spec[key][2]
  v=frame[cols].to_numpy(float)
  vals.extend(float(np.max(np.abs(v[:,i]-v[:,j]))) for i,j in PAIRS)
 LIMITS[key]=max(vals)
v=omni[omni_spec['nivel_relativo'][2]].to_numpy(float)
LIMITS['nivel_relativo']=max(float(np.max(np.abs(v[:,i]-v[:,j]))) for i,j in PAIRS)

made=[]
rows=[]

def lineplot(df, cols, title, unit, fn, subtitle=''):
 vals=np.asarray(df[cols],dtype=float)
 assert vals.shape==(25,3) and np.isfinite(vals).all(), (fn, vals.shape)
 x=np.arange(1,26)
 fig,ax=plt.subplots(figsize=(12.5,5.15))
 for i,label in enumerate(SIMS):
  ax.plot(x, vals[:,i], marker='o', markersize=3.6, linewidth=1.7,label=label)
 ax.set_xticks(x)
 ax.set_xticklabels([f'R{n:02d}' for n in x],rotation=50,ha='right')
 ax.set_xlabel('Identificador do receptor')
 ax.set_ylabel(f'{title.split(" (")[0]} ({unit})')
 fig.suptitle(title,fontweight='bold',y=.970,fontsize=14)
 if subtitle:
  fig.text(.5,.914,subtitle,fontsize=9,ha='center',va='center')
 ax.grid(alpha=.24, linewidth=.7)
 ax.legend(ncol=3,loc='upper center',bbox_to_anchor=(.5,-.26),frameon=False)
 fig.subplots_adjust(left=.1,right=.98,top=.83,bottom=.30)
 p=FIG/fn
 fig.savefig(p,dpi=180)
 plt.close(fig)
 made.append(p)

def heatmap(delta, names, unit, title, filename, clim, xcoords, ycoords):
 assert len(delta)==25
 mat=np.full((5,5),np.nan)
 labels=np.full((5,5),'',dtype=object)
 xs=sorted(set(np.round(xcoords,3)))
 ys=sorted(set(np.round(ycoords,3)))
 assert len(xs)==5 and len(ys)==5
 for i in range(25):
  ix=xs.index(round(xcoords[i],3)); iy=ys.index(round(ycoords[i],3))
  mat[ix,iy]=delta[i]
  labels[ix,iy]=names[i]
 assert np.isfinite(mat).all()
 fig, ax=plt.subplots(figsize=(6.3,5.05))
 img=ax.imshow(mat,origin='lower',norm=TwoSlopeNorm(vmin=-clim,vcenter=0,vmax=clim),interpolation='nearest',aspect='equal',cmap='coolwarm')
 ax.set_title(title,fontweight='bold',pad=10)
 ax.set_xticks(range(5), [f'{x:g}' for x in ys])
 ax.set_yticks(range(5), [f'{x:.3f}' for x in xs])
 ax.set_xlabel('Y (m) — posição transversal')
 ax.set_ylabel('X (m) — avanço na sala')
 for r in range(5):
  for c in range(5):
   value=mat[r,c]
   ax.text(c,r,f'{value:+.2f}\n{labels[r,c]}',ha='center',va='center',fontsize=7.4,
           color='black',bbox=dict(boxstyle='round,pad=.12',facecolor='white',alpha=.67,edgecolor='none'))
 cb=fig.colorbar(img,ax=ax,fraction=.042,pad=.04)
 cb.set_label(f'Diferença ({unit})')
 fig.tight_layout()
 p=PAIR_DIR/filename
 fig.savefig(p,dpi=170)
 plt.close(fig)
 made.append(p)
 return p

def combine(paths,outfile,full_title,description):
 panels=[Image.open(p).convert('RGB') for p in paths]
 # consistent sizes, keeping pixel-perfect values in original plots
 maxw=max(p.width for p in panels)
 maxh=max(p.height for p in panels)
 bg=Image.new('RGB',(maxw*3+100, maxh+150),'white')
 drawer=ImageDraw.Draw(bg)
 try:
  font_title=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',28)
  font_sub=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
 except OSError:
  font_title=ImageFont.load_default();font_sub=font_title
 drawer.text((50,24),full_title,fill=(25,30,40),font=font_title)
 drawer.text((50,65),description,fill=(90,90,90),font=font_sub)
 for i,p in enumerate(panels):
  xx=25+i*(maxw+25)
  bg.paste(p,(xx,102))
  if i<2:
   drawer.line((xx+maxw+8,102,xx+maxw+8,102+maxh),fill=(230,230,230),width=2)
 p=FIG/outfile
 bg.save(p,optimize=True)
 made.append(p)

def run_case(df,case_key,case_label,spec, rc, xc,yc, is_level=False):
 # Ensure exported values are aligned across all series, with no hidden interpolation.
 for key,(metric,unit,cols) in spec.items():
  vals=df[cols].apply(pd.to_numeric,errors='raise').to_numpy(float)
  assert vals.shape==(25,3) and np.isfinite(vals).all(), (case_key,key)
  title=f'{case_label} — {metric}'
  subtitle=('Comparação de perfis centrados; ODEON usa G e os demais usam nível/SPL relativos.'
           if key=='nivel_relativo' else 'Valores dos três simuladores na banda de oitava de 1000 Hz.')
  lineplot(df,cols,title,unit,f'novo_linhas_{case_key}_{key}.png',subtitle)
  arrs=[]
  diffs=np.column_stack([vals[:,i]-vals[:,j] for i,j in PAIRS])
  clim=LIMITS[key]
  for k,(i,j) in enumerate(PAIRS):
   a,b=SIMS[i],SIMS[j]
   delta=diffs[:,k]
   names=[f'R{n:02d}' for n in range(1,26)]
   p=heatmap(delta,names,unit,f'{a} − {b}',f'novo_mapa_{case_key}_{key}_{i}{j}.png',clim,
             df[xc].to_numpy(float),df[yc].to_numpy(float))
   arrs.append(p)
   rows.append({'caso':case_key,'metrica':key,'unidade':unit,'par':f'{a} − {b}','N':25,
               'vies':float(delta.mean()),'MAE':float(np.mean(np.abs(delta))),
               'RMSE':float(np.sqrt(np.mean(delta**2))),
               'maior_diferenca_abs':float(np.max(np.abs(delta))),'escala_comum_abs':clim})
  combine(arrs,f'novo_pareados_{case_key}_{key}.png',f'{case_label}: {metric}',
          f'Diferenças com sinal (primeiro − segundo), em {unit}. Escala comum aos pares e cenários.')

run_case(omni,'omni','OMNI',omni_spec,'Receptor','X_m','Y_m')
for angle in (0,-8,-16):
 c=cf2[cf2.angle==angle].sort_values('receiver').reset_index(drop=True)
 run_case(c,f'cf2_{angle:+03d}'.replace('+','p').replace('-','m'),f'CF2 Bosch {angle}°',cf2_spec,'receiver','x','y')

pair=pd.DataFrame(rows)
pair.to_csv(DATA/'novo_comparacoes_pareadas_mapas.csv',index=False,float_format='%.9f')
# A compact summary of means for easy reference in text.
means=[]
for case_key,df,spec in [('OMNI',omni,omni_spec)]+[(f'CF2 {angle}°',cf2[cf2.angle==angle],cf2_spec) for angle in (0,-8,-16)]:
 for key,(metric,unit,cols) in spec.items():
  means.append(dict(caso=case_key,metrica=metric,unidade=unit,ODEON=float(df[cols[0]].mean()),
  ISimpa=float(df[cols[1]].mean()),Pyroom=float(df[cols[2]].mean())))
pd.DataFrame(means).to_csv(DATA/'novo_respostas_2d_medias.csv',index=False,float_format='%.9f')
print(f'NOVOS ARQUIVOS: {len(made)} (linhas 13, mapas pareados individuais 39, painéis 13)')
print('PAINÉIS e LINHAS',len(list(FIG.glob('novo_pareados_*.png'))),len(list(FIG.glob('novo_linhas_*.png'))))
print(pair[['caso','metrica','par','vies','RMSE']].round(3).to_string(index=False))
