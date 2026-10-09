#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct-field benchmark PRA 0.10.1 vs Windows SPPS using one Bosch CF2.

Ready cases: PRA_OMNI_16K, PRA_CF2_V5_16K; optional PRA_CF2_FAST_16K.
Optional GeneralFIR SOFA (once generated): PRA_SOFA_FIR_NATIVE_16K.
The 6M TF / 6G-E engines need Bosch SOFA inputs and are a separate phase.

Method: the complex 1000-Hz frequency response is sampled from the ISM RIR
(max_order=0). One single *OMNI* level offset is fitted to the analytical
point-source 1/r reference and applied UNCHANGED to all directional cases.
The directional DIFFERENCE (CF2 - OMNI) does not depend on that offset.

Important limitations: SPPS uses spheres (radius 0.31m), PRA point microphones;
R8 overlaps the source and is excluded from summary but retained in CSV.
CF2 v1 contains magnitude only, not measured phase. An optional synthetic-phase
GeneralFIR must be labeled synthetic, not a phase-measured Bosch result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.directivities import Directivity

BANDS_16K = np.array([125, 250, 500, 1000, 2000, 4000, 8000], dtype=float)


def load_python_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_spps(path: Path) -> dict:
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    out = {row['receiver']: row for row in rows}
    if len(out) != len(rows) or len(out) != 25:
        raise ValueError(f"Expected 25 unique receivers in {path}; got {len(out)}")
    return out


def position(row):
    return np.array([float(row[k]) for k in ('x_m', 'y_m', 'z_m')], dtype=float)


def get_verified_inputs(omni_path: Path, cf2_path: Path):
    omni = load_spps(omni_path)
    cf2 = load_spps(cf2_path)
    names = [f'Receiver {i}' for i in range(1, 26)]
    if set(omni) != set(names) or set(cf2) != set(names):
        raise ValueError('Receiver IDs differ from expected Receiver 1 .. 25')
    for n in names:
        a, b = omni[n], cf2[n]
        if not np.allclose(position(a), position(b), rtol=0, atol=1e-5):
            raise ValueError(f'Coordinates differ for {n}')
        for key in ('frequency_hz', 'lw_db', 'radius_m'):
            if abs(float(a[key]) - float(b[key])) > (2e-3 if key == 'lw_db' else 1e-6):
                raise ValueError(f'M0 and CF2 differ in {key} for {n}')
        if float(a['frequency_hz']) != 1000:
            raise ValueError('This benchmark is specifically for 1000 Hz')
    mic_matrix = np.column_stack([position(omni[n]) for n in names])
    return names, omni, cf2, mic_matrix


class CF2V5MagnitudeDirectivity(Directivity):
    """Reference CF2 magnitude model; local axes +X front,+Y left,+Z top.

    CF2 indices: rotation around forward axis 0->up,90->left, arc 0->front.
    Same interpolation choice as CF2_FAST: bilinear in dB then /20 to amplitude.
    7-band response for PRA 0.10.1 multi-band ISM (shape 7,n_directions).
    """
    def __init__(self, parser_module, path: Path):
        cf = parser_module.CF2File(path)
        self.cf2 = cf
        self.balloon = np.stack([
            cf.directivity_grid(float(f))[2] for f in BANDS_16K
        ]).astype(np.float64)
        if self.balloon.shape != (7,72,37) or not np.all(np.isfinite(self.balloon)):
            raise ValueError('Unexpected CF2 7-band shape or nonfinite values')
        self.speaker_orientation = '+X front, +Y left, +Z up'

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def gain_db_from_world(self, world_vectors: np.ndarray):
        v = np.asarray(world_vectors, dtype=float).reshape(-1,3)
        v = v / np.linalg.norm(v, axis=1)[:, None]
        x,y,z = v[:,0], v[:,1], v[:,2]
        arcs = np.degrees(np.arccos(np.clip(x,-1,1))) / 5
        rotation = np.degrees(np.arctan2(y,z)) % 360
        rotation = np.where(np.hypot(y,z) < 1e-12, 0.0, rotation)
        rots = rotation / 5
        r0 = np.floor(rots).astype(int) % 72
        r1 = (r0 + 1) % 72
        a0 = np.floor(arcs).astype(int).clip(0,36)
        a1 = (a0 + 1).clip(0,36)
        wr = (rots - np.floor(rots))[None,:]
        wa = (arcs - np.floor(arcs))[None,:]
        wa = np.where(a0[None,:] == 36, 0, wa)
        q00 = self.balloon[:, r0, a0]
        q10 = self.balloon[:, r1, a0]
        q01 = self.balloon[:, r0, a1]
        q11 = self.balloon[:, r1, a1]
        return (1-wa)*((1-wr)*q00 + wr*q10) + wa*((1-wr)*q01 + wr*q11)

    def get_response(self, azimuth, colatitude=None, magnitude=False, degrees=True):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))
        col = (np.full_like(az, 90 if degrees else np.pi/2) if colatitude is None
               else np.atleast_1d(np.asarray(colatitude, dtype=float)))
        az, col = np.broadcast_arrays(az,col)
        if degrees:
            az,col = np.deg2rad(az),np.deg2rad(col)
        v = np.column_stack((np.sin(col.ravel())*np.cos(az.ravel()),
                             np.sin(col.ravel())*np.sin(az.ravel()),
                             np.cos(col.ravel())))
        return 10**(self.gain_db_from_world(v)/20.0)

    def sample_rays(self, n_rays, rng=None):
        raise NotImplementedError('ISM only; max_order=0')


def f_response(rir, frequency=1000, fs=16000):
    rir = np.asarray(rir, dtype=float)
    return complex(np.sum(rir * np.exp(-2j*np.pi*frequency*np.arange(rir.size)/fs)))


def run_pra_case(directivity, microphones, fs=16000):
    bands = [125,250,500,1000,2000,4000,8000]
    mat = pra.Material(energy_absorption={'coeffs':[0.0]*7,'center_freqs':bands})
    room = pra.ShoeBox([6,5,3], fs=fs, materials=mat, max_order=0)
    if not room.is_multi_band:
        raise RuntimeError('Room must be multi-band (7 bands)')
    if directivity is None:
        room.add_source([2.0,2.5,1.5])
    else:
        room.add(pra.SoundSource([2.0,2.5,1.5], directivity=directivity))
    room.add_microphone_array(microphones)
    start = time.perf_counter()
    room.compute_rir()
    duration = time.perf_counter() - start
    h = np.array([f_response(room.rir[k][0],fs=fs) for k in range(microphones.shape[1])])
    if not np.all(np.isfinite(h)) or np.any(np.abs(h) <= 0):
        raise RuntimeError('Nonfinite or zero RIR transfer at 1000 Hz')
    return h,duration


def metric(arr):
    a=np.asarray(arr,dtype=float)
    return {'rmse_db':float(np.sqrt(np.mean(a*a))),
            'mae_db':float(np.mean(np.abs(a))),
            'bias_db':float(np.mean(a)),
            'max_abs_db':float(np.max(np.abs(a)))}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cf2',type=Path,required=True)
    ap.add_argument('--parser-v5',type=Path,required=True)
    ap.add_argument('--sp-omni',type=Path,required=True)
    ap.add_argument('--sp-cf2',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--fast-module',type=Path,default=None)
    ap.add_argument('--fast-module-dir',type=Path,default=None)
    args=ap.parse_args()

    names,sp_omni,sp_cf2,mics=get_verified_inputs(args.sp_omni,args.sp_cf2)
    parser=load_python_file(args.parser_v5,'benchmark_cf2_parser_v5')
    refdir=CF2V5MagnitudeDirectivity(parser,args.cf2)
    # Important: this reference is the directivity in the CF2 binary, with no
    # conversion to EASE or a synthetic propagation model.
    source=np.array([2.0,2.5,1.5])
    rel=mics.T-source
    norm=np.linalg.norm(rel,axis=1)
    input_db=refdir.gain_db_from_world(rel)  # 7 x 25
    refdb=input_db[3]

    print(f'PRA {pra.__version__}; 25 receivers, direct-field only, 1000 Hz')
    print('Reference CF2 SHA256',sha256(args.cf2))
    results={}
    h_omni,t=run_pra_case(None,mics)
    results['PRA_OMNI_16K']={'h':h_omni,'compute_rir_s':t}
    h_v5,t=run_pra_case(refdir,mics)
    results['PRA_CF2_V5_16K']={'h':h_v5,'compute_rir_s':t}

    if args.fast_module:
        module_dir = args.fast_module_dir or args.fast_module.parent
        sys.path.insert(0,str(module_dir.resolve()))
        fast_module=load_python_file(args.fast_module,'benchmark_cf2_fast')
        fastdir=fast_module.CF2SevenBandDirectivityFast(
            cf2_path=str(args.cf2), speaker_azimuth_deg=0,
            speaker_colatitude_deg=90, speaker_roll_deg=0,
            rotation_handedness='top_to_left', interpolation_domain='db')
        h,t=run_pra_case(fastdir,mics)
        results['PRA_CF2_FAST_16K']={'h':h,'compute_rir_s':t}

        # Compare fast raw gain against parser v5 reference at each receiver;
        # both should agree to within interpolation/quantization precision.
        world_az=np.degrees(np.arctan2(rel[:,1],rel[:,0]))
        world_col=np.degrees(np.arccos(np.clip(rel[:,2]/norm,-1,1)))
        fast_response=fastdir.get_response(world_az,world_col,degrees=True)
        diff=20*np.log10(np.maximum(fast_response[3],1e-30))-refdb
        print('Fast adapter vs CF2 v5 at 1000 Hz: max|diff| = %.6f dB' % max(abs(diff)))
        if max(abs(diff)) > .15:
            warnings.warn('Fast adapter differs from v5 parser >0.15 dB; examine its binary offset and orientation before accepting results')

    # A single global offset from OMNI analytic 1/r calibration is used for
    # all directivity models. This is *not* an independent CF2 fitting step.
    valid=np.array([str(sp_omni[n]['source_inside_receiver_sphere']).lower()=='false' for n in names])
    analytic=np.array([float(sp_omni[n]['spl_freefield_db']) for n in names])
    raw_omni=20*np.log10(np.abs(h_omni))
    cal=float(np.median(analytic[valid]-raw_omni[valid]))
    print('One OMNI calibration scalar: %.6f dB, applied unchanged to CF2' % cal)
    sp_o=np.array([float(sp_omni[n]['spl_spps_db']) for n in names])
    sp_c=np.array([float(sp_cf2[n]['spl_spps_db']) for n in names])
    sp_effect=sp_c-sp_o

    args.out.mkdir(parents=True,exist_ok=True)
    writer_fields=['receiver','x_m','y_m','z_m','distance_m','exclude_source_overlap',
                   'spps_omni_db','spps_cf2_db','spps_cf2_minus_omni_db',
                   'freefield_omni_db','cf2_v5_input_gain_db']
    for case in results:
        writer_fields += [f'{case}_db',f'{case}_minus_spps_db']
        if case!='PRA_OMNI_16K':
            writer_fields += [f'{case}_minus_pra_omni_db',f'{case}_effect_error_vs_spps_db']

    output=[]
    for i,name in enumerate(names):
        row={'receiver':name, 'x_m':mics[0,i], 'y_m':mics[1,i], 'z_m':mics[2,i],
             'distance_m':norm[i], 'exclude_source_overlap':not valid[i],
             'spps_omni_db':sp_o[i], 'spps_cf2_db':sp_c[i],
             'spps_cf2_minus_omni_db':sp_effect[i],
             'freefield_omni_db':analytic[i], 'cf2_v5_input_gain_db':refdb[i]}
        for case,res in results.items():
            db=20*np.log10(abs(res['h'][i]))+cal
            row[f'{case}_db']=db
            row[f'{case}_minus_spps_db']=db-(sp_o[i] if case=='PRA_OMNI_16K' else sp_c[i])
            if case!='PRA_OMNI_16K':
                effect_db=20*np.log10(abs(res['h'][i])/abs(h_omni[i]))
                row[f'{case}_minus_pra_omni_db']=effect_db
                row[f'{case}_effect_error_vs_spps_db']=effect_db-sp_effect[i]
        output.append(row)

    with (args.out/'comparison_25_receivers.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=writer_fields)
        w.writeheader();w.writerows(output)

    summary={'method':'complex RIR transfer at 1000 Hz, one calibration from PRA OMNI to free-field',
             'source_position_m':[2,2.5,1.5],'n_receivers':25,'n_valid':int(valid.sum()),
             'excluded_source_overlap':[names[i] for i in range(25) if not valid[i]],
             'cf2_sha256':sha256(args.cf2),'spps_omni_csv_sha256':sha256(args.sp_omni),
             'spps_cf2_csv_sha256':sha256(args.sp_cf2),
             'calibration_omni_offset_db':cal,
             'spps_cf2_effect_valid':metric(sp_effect[valid]),
             'cases':{}}
    for case,res in results.items():
        h=res['h']; lvl=20*np.log10(abs(h))+cal
        reference=sp_o if case=='PRA_OMNI_16K' else sp_c
        item={'compute_rir_s':res['compute_rir_s'],
              'pra_minus_spps':metric((lvl-reference)[valid])}
        if case!='PRA_OMNI_16K':
            effect=20*np.log10(abs(h)/abs(h_omni))
            item['pra_effect_vs_spps_effect']=metric((effect-sp_effect)[valid])
            item['pra_effect_vs_cf2_input_gain']=metric((effect-refdb)[valid])
        summary['cases'][case]=item
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print('\nCASE'.ljust(27),'TIME', 'RMSE vs SPPS', 'Effect RMSE vs SPPS')
    for case,res in summary['cases'].items():
        x=res['pra_minus_spps']['rmse_db']
        er=res.get('pra_effect_vs_spps_effect',{}).get('rmse_db',float('nan'))
        print(f'{case:<27} {res["compute_rir_s"]:.3f}s  {x:.3f}dB  {er:.3f}dB')
    print('Files:',args.out/'comparison_25_receivers.csv',args.out/'summary.json')
    print('CAUTION: CF2 v1 magnitude-only. SPPS sphere vs PRA point microphone; one OMNI calibration only.')


if __name__=='__main__':
    main()
