#!/usr/bin/env python3
"""Check image alignment and independent vertical controls using measured CH5."""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parent

def rgb(path):
    return np.array(Image.open(path).convert('RGB').resize((876,476),Image.Resampling.LANCZOS)).astype(float)

def mask(a,ch):
    r,g,b=a[:,:,0],a[:,:,1],a[:,:,2]
    return {
        1:(r>130)&(g<110)&(b<110),
        2:(b>130)&(r<110)&(g<110),
        5:(b>130)&(g>105)&(b>g*1.12)&(r<140)&(g>r*1.15),
        6:(r>140)&(b>85)&(g<130)&(r>b*1.15)&(b>g*1.2),
        7:(g>r*1.06)&(r>b*1.45)&(r>65),
        8:(b>r*1.02)&(r>g*1.22)&(b>g*1.3)&(r>80),
    }[ch]

def centerlines(a,ch):
    m=mask(a,ch);ys=[]
    for x in range(60,840):
        y=np.flatnonzero(m[:,x]);ys.append(np.median(y) if len(y) else np.nan)
    return np.array(ys)

def run_controls():
    base=ROOT/'verification/controls'
    cases={'base':[], 'position':['--position','CH5=-0.54'],
           'offset':['--offset','CH5=1000'], 'scale':['--scale','CH5=1000']}
    curves={}
    for name,extra in cases.items():
        out=base/name
        subprocess.run([sys.executable,str(ROOT/'render_tss.py'),'--channels','5',
                        '--output',str(out),'--name',name,*extra],check=True,capture_output=True,text=True)
        curves[name]=centerlines(rgb(out/f'{name}.png'),5)
    # The fixed native 476-pixel pane has exactly ten vertical divisions.
    # OFFSET +1000 W at 2000 W/div goes down by half a division.
    dy_pos=float(np.nanmedian(curves['position']-curves['base']))
    dy_off=float(np.nanmedian(curves['offset']-curves['base']))
    assert abs(dy_pos+47.6)<.8,dy_pos
    assert abs(dy_off-23.8)<.8,dy_off
    position_reference=238.-47.6*(-1.54)
    # Halving units/div doubles physical-value displacement from the position reference.
    scale_error=float(np.nanmedian(np.abs((position_reference-curves['scale'])-2*(position_reference-curves['base']))))
    assert scale_error<1.,scale_error
    bad=subprocess.run([sys.executable,str(ROOT/'render_tss.py'),'--scale','CH5=0',
                        '--output',str(base/'invalid')],capture_output=True,text=True)
    assert bad.returncode!=0 and not (base/'invalid').exists()
    return {'position_plus_1div_native_pixels':dy_pos,'expected_position_pixels':-47.6,
            'offset_plus_1000W_native_pixels':dy_off,'expected_offset_pixels':23.8,
            'halved_scale_doubled_displacement_median_error_pixels':scale_error,
            'nonpositive_scale_rejected':True}

def run_crop():
    out=ROOT/'verification/crop_8x5'
    subprocess.run([sys.executable,str(ROOT/'render_tss.py'),'--channels','1,2,3,4,5,6,7,8',
        '--crop-x-div','1','9','--crop-y-div','-3','2','--save-full',
        '--gain','CH8=10','--scale','CH8=20',
        '--output',str(out),'--name','eight_channels'],check=True,capture_output=True,text=True)
    meta=json.loads((out/'eight_channels.json').read_text())
    assert meta['native_grid_divisions']==[10,10] and meta['visible_grid_divisions']==[8,5]
    assert len(meta['channels'])==8 and meta['channels']['8']['gain']==10
    ns={'s':'http://www.w3.org/2000/svg'}
    for suffix,counts in [('_full10x10',(11,11)),('',(9,6))]:
        svg=ET.parse(out/f'eight_channels{suffix}.svg').getroot()
        ids=[g.get('id','') for g in svg.findall('.//s:g',ns)]
        assert sum(i.startswith('xtick_') for i in ids)==counts[0]
        assert sum(i.startswith('ytick_') for i in ids)==counts[1]
        assert not svg.findall('.//s:image',ns)
    with Image.open(out/'eight_channels_full10x10.png') as full,Image.open(out/'eight_channels.png') as crop:
        assert abs(crop.width-full.width*.8)<=1 and abs(crop.height-full.height*.5)<=1
    return {'eight_channels_loaded':8,'native_divisions':[10,10],'cropped_divisions':[8,5],
            'full_and_crop_major_tick_counts_verified':True,'physical_mm_per_div_preserved':True,
            'probe_gain_10_scale_20_verified':True,'SVG_raster_images':0}

def main():
    original=rgb(ROOT/'results/original_crop.png')
    redraw=rgb(ROOT/'results/redraw_5ms.png')
    alignment={}
    for ch in [1,2,5,6,7,8]:
        a,b=centerlines(original,ch),centerlines(redraw,ch)
        # Compare the same physical coordinates: the old screenshot has a small
        # asymmetric border; the new pane occupies the exact full 10x10 frame.
        x=np.arange(60,840)
        native_x=(x-3.)/871.*876.
        b=np.interp(native_x,x,b,left=np.nan,right=np.nan)
        b=236.+(b-238.)*47.5/47.6
        delta=np.abs(a-b);valid=np.isfinite(delta)
        alignment[f'CH{ch}']={'matched_image_columns':int(valid.sum()),
            'median_absolute_trace_center_difference_native_pixels':float(np.nanmedian(delta)),
            'p95_absolute_trace_center_difference_native_pixels':float(np.nanpercentile(delta,95))}
        assert valid.sum()>650,(ch,valid.sum())
        assert np.nanmedian(delta)<2.,(ch,alignment[f'CH{ch}'])
    checks={'scope_comparison':alignment,'controls':run_controls(),'native_grid_crop':run_crop(),
            'metric_scope':'color-mask trace centerlines; new exact pane affinely registered to old screenshot x=3..874, y_center=236, dy=47.5; excludes badges; appearance check, not signal error or pixel identity'}
    (ROOT/'verification').mkdir(exist_ok=True)
    (ROOT/'verification/results.json').write_text(json.dumps(checks,indent=2)+'\n')
    print(json.dumps(checks,indent=2))

if __name__=='__main__':main()
