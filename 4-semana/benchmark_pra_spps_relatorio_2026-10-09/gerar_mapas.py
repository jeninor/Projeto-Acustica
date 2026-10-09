#!/usr/bin/env python3
"""Gera mapas espaciais (PRA vs SPPS) exclusivamente a partir de dados medidos.
Uso: python gerar_mapas.py (executar na pasta deste arquivo).
"""
from pathlib import Path
import csv
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm, Normalize
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent
CSV = ROOT / 'dados' / 'comparison_25_receivers_original.csv'
FIGS = ROOT / 'figuras'
FIGS.mkdir(exist_ok=True)
with CSV.open(encoding='utf-8-sig', newline='') as fh:
    rows = [r for r in csv.DictReader(fh) if r.get('receiver','').strip()]
assert len(rows) == 25, f'São esperados 25 receptores, obtidos {len(rows)}'

xs = sorted({float(r['x_m']) for r in rows})
ys = sorted({float(r['y_m']) for r in rows})
assert len(xs)==len(ys)==5

FIELDS = [
 ('mapa_erro_omni.png', 'Erro espacial · OMNI',
  'Pyroomacoustics − SPPS, 1000 Hz (dB)', 'PRA_OMNI_16K_minus_spps_db',
  'signed', .5, 'Diferença pontual pequena; RMSE = 0,187 dB.'),
 ('mapa_erro_cf2.png', 'Erro espacial · CF2 Bosch LA1_UW24',
  'Pyroomacoustics − SPPS, 1000 Hz (dB)', 'PRA_CF2_V5_16K_minus_spps_db',
  'signed', 2.5, 'Maior divergência no R13: +2,126 dB.'),
 ('mapa_erro_efeito_diretividade.png', 'Divergência do efeito direcional · CF2 − OMNI',
  '(PRA_CF2 − PRA_OMNI) − (SPPS_CF2 − SPPS_OMNI), dB', 'PRA_CF2_V5_16K_effect_error_vs_spps_db',
  'signed', 2.5, 'Mede a diferença de atenuação angular entre simuladores; RMSE = 0,786 dB.'),
 ('mapa_magnitude_divergencia_cf2.png', 'Zonas de maior divergência · CF2',
  '|Pyroomacoustics − SPPS|, 1000 Hz (dB)', 'PRA_CF2_V5_16K_minus_spps_db',
  'absolute', 2.5, 'Células são locais amostrados, não um campo contínuo interpolado.'),
]

plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 11,
    'axes.labelcolor':'#263746', 'text.color':'#203040',
    'axes.edgecolor':'#94a3b8','xtick.color':'#334155','ytick.color':'#334155',
    'figure.facecolor':'white', 'savefig.facecolor':'white'
})

def plot_map(filename, title, subtitle, col, mode, lim, footnote):
    values = np.full((5,5), np.nan)
    labels = [['' for _ in range(5)] for __ in range(5)]
    for r in rows:
        x,y=float(r['x_m']), float(r['y_m'])
        ix,iy = xs.index(x), ys.index(y)
        labels[iy][ix] = r['receiver'].replace('Receiver ','R')
        excluded = r['exclude_source_overlap'].strip().lower() in ('true','1','yes')
        if not excluded:
            v = float(r[col]); values[iy,ix] = abs(v) if mode=='absolute' else v
    cmap=plt.get_cmap('YlOrRd' if mode=='absolute' else 'RdBu_r').copy()
    cmap.set_bad('#f1f5f9')
    norm = Normalize(vmin=0, vmax=lim) if mode == 'absolute' else TwoSlopeNorm(vmin=-lim,vcenter=0,vmax=lim)
    fig, ax=plt.subplots(figsize=(10.6,7.3),dpi=180)
    im=ax.imshow(values,origin='lower',cmap=cmap,norm=norm,extent=[.25,5.25,0,5],interpolation='nearest',aspect='equal')
    # Etiquetas: identificar receptores, valores amostrados e a exclusão R8.
    for iy,y in enumerate(ys):
        for ix,x in enumerate(xs):
            rid=labels[iy][ix]
            val=values[iy,ix]
            if np.isfinite(val):
                vstr = f'{val:.2f}' if mode=='absolute' else f'{val:+.2f}'
                # Contraste por legibilidade e intensidade.
                dark = (mode == 'signed' and abs(val)>lim*.66) or (mode=='absolute' and val>lim*.58)
                color='white' if dark else '#12283b'
                ax.text(x,y+.12,rid,ha='center',va='center',weight='bold',fontsize=10.2,color=color)
                ax.text(x,y-.13,vstr,ha='center',va='center',fontsize=10.1,color=color)
            else:
                ax.add_patch(Rectangle((x-.5,y-.5),1,1,facecolor='#e2e8f0',edgecolor='#cbd5e1',hatch='///',lw=.5))
                ax.text(x,y+.12,rid,ha='center',va='center',weight='bold',fontsize=10,color='#475569')
                ax.text(x,y-.16,'excluído',ha='center',va='center',fontsize=9,color='#475569')
    ax.scatter([2],[2.5],marker='*',s=190,c='#ffdf65',ec='#1e293b',lw=1.1,zorder=5)
    ax.set_xlim(0,5.4); ax.set_ylim(-.12,5.1)
    ax.set_xticks(xs); ax.set_yticks(ys)
    ax.set_xticklabels([f'{x:g}' for x in xs]); ax.set_yticklabels([f'{y:g}' for y in ys])
    ax.set_xlabel('Coordenada x dos receptores (m)',labelpad=10)
    ax.set_ylabel('Coordenada y dos receptores (m)',labelpad=10)
    ax.set_title(title+'\n'+subtitle, loc='left', fontsize=15, fontweight='bold',pad=23)
    ax.grid(False)
    for xv in np.arange(.25,5.3,1.):ax.axvline(xv,c='#f8fafc',lw=1.5,alpha=.85)
    for yv in np.arange(0,5.1,1.):ax.axhline(yv,c='#f8fafc',lw=1.5,alpha=.85)
    cb=fig.colorbar(im,ax=ax,fraction=.044,pad=.03)
    cb.set_label('|Δ SPL| (dB)' if mode=='absolute' else 'Δ SPL (dB)',labelpad=13)
    fig.text(.13,.08,'★ Fonte: (2,00; 2,50; 1,50) m     |     R8: esfera de recepção sobrepõe a fonte (fora das métricas)', fontsize=9.3,color='#475569')
    fig.text(.13,.050,footnote,fontsize=9.1,color='#64748b')
    fig.subplots_adjust(top=.86,bottom=.18,left=.10,right=.90)
    dest=FIGS/filename
    fig.savefig(dest,bbox_inches='tight',dpi=180)
    plt.close(fig)
    print(dest.name, dest.stat().st_size)

for case in FIELDS:
    plot_map(*case)
