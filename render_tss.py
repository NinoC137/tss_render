#!/usr/bin/env python3
"""Offline scope-style drawing from measured TSS samples; never accesses hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from matplotlib.ticker import FixedLocator
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
READER = ROOT/'vendor'
DISPLAY_ROOT = ROOT/'reference'
COLORS = {1:'#e10000', 2:'#0000e1', 3:'#4c9ec9', 4:'#8f58a0', 5:'#4c9ec9', 6:'#dd3496', 7:'#849b35', 8:'#8f58a0'}
plt.rcParams.update({'path.simplify':False, 'svg.fonttype':'none', 'font.family':'DejaVu Sans'})
GRID_DIVISIONS = 10
BADGE_FONT = 'DejaVu Sans Mono'
# Latest user reference: inactive C1 pale red, selected C2 blue, remaining labels pale.
BADGE_STYLES = {
    1: {'edge':'#ff0000', 'face':'#ffe1de', 'text':'#e10000'},
    2: {'edge':'#0000ff', 'face':'#0000ff', 'text':'#ffffff'},
    3: {'edge':'#4c9ec9', 'face':'#edf5f9', 'text':'#151515'},
    4: {'edge':'#8f58a0', 'face':'#f3edf6', 'text':'#151515'},
    5: {'edge':'#4c9ec9', 'face':'#edf5f9', 'text':'#151515'},
    6: {'edge':'#dd3496', 'face':'#fcebf5', 'text':'#151515'},
    7: {'edge':'#849b35', 'face':'#f3f5e9', 'text':'#151515'},
    8: {'edge':'#8f58a0', 'face':'#f3edf6', 'text':'#151515'},
}


def validate_crop(x_div=(0.,10.), y_div=(-5.,5.)):
    """Crop a fixed native pane; divisions are never regenerated for the crop."""
    x0,x1=map(float,x_div); y0,y1=map(float,y_div)
    if not all(np.isfinite(v) for v in (x0,x1,y0,y1)):
        raise ValueError('crop bounds must be finite')
    if not -1e-9<=x0<x1<=10+1e-9 or not -5-1e-9<=y0<y1<=5+1e-9:
        raise ValueError('crop must lie inside x=0..10 div, y=-5..5 div')
    return (x0,x1),(y0,y1)


def configure_scope_axes(ax, xlim, time_per_div, ylim=(-5.,5.), *,
                         base_x_start=None, color='#b5b5b5', linewidth=.4):
    """Shared 10x10 graticule for native exports and publication crops.

    x uses caller time units; y always uses physical display divisions.
    A shorter view is a crop of the original ticks, never a new ten-cell grid.
    """
    x0,x1=map(float,xlim); y0,y1=map(float,ylim)
    if not np.isfinite(time_per_div) or time_per_div<=0:
        raise ValueError('time per division must be positive')
    origin=x0 if base_x_start is None else float(base_x_start)
    if x0<origin-1e-9 or x1>origin+10*time_per_div+1e-9:
        raise ValueError('horizontal view exceeds the native ten-division pane')
    validate_crop(((x0-origin)/time_per_div,(x1-origin)/time_per_div),(y0,y1))
    xticks=origin+np.arange(11)*time_per_div
    yticks=np.arange(-5.,6.)
    ax.set_xlim(x0,x1);ax.set_ylim(y0,y1)
    ax.xaxis.set_major_locator(FixedLocator(xticks))
    ax.yaxis.set_major_locator(FixedLocator(yticks))
    ax.minorticks_off()
    ax.tick_params(bottom=False,left=False,labelbottom=False,labelleft=False)
    ax.grid(True,color=color,linestyle=(0,(4,4)),linewidth=linewidth)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(.45);spine.set_color('#999999')
    ax._scope_grid={'native_divisions':[10,10],
        'visible_divisions':[(x1-x0)/time_per_div,y1-y0],
        'time_per_div':float(time_per_div),'base_x_start':origin,
        'x_limits':[x0,x1],'y_limits_div':[y0,y1],
        'visible_major_x':[float(t) for t in xticks if x0-1e-9<=t<=x1+1e-9],
        'visible_major_y':[float(t) for t in yticks if y0-1e-9<=t<=y1+1e-9]}
    return ax._scope_grid


def draw_channel_badge(ax, channel, reference, *, size=6.8,
                       width_mm=None, height_mm=None, side='left', label_shift_div=0):
    """Scope-style pentagon; small monospaced regular text, explicit per-channel fill."""
    style=BADGE_STYLES[channel]
    fw,fh=ax.figure.get_size_inches()*25.4
    w=fw*ax.get_position().width; h=fh*ax.get_position().height
    ww=(width_mm/w) if width_mm is not None else .40/10
    hh=(height_mm/h/2) if height_mm is not None else .50/10/2
    yy=(reference+label_shift_div-ax.get_ylim()[0])/(ax.get_ylim()[1]-ax.get_ylim()[0])
    # The reference row remains exact even when the pane is vertically cropped.
    if yy<0 or yy>1:return None
    xx=.006
    points=np.array([[xx,yy-hh],[xx+ww*.70,yy-hh],[xx+ww,yy],
                     [xx+ww*.70,yy+hh],[xx,yy+hh]])
    text_x=xx+ww*.43
    if side=='right':points[:,0]=1-points[:,0];text_x=1-text_x
    elif side!='left':raise ValueError('badge side must be left or right')
    patch=Polygon(points,transform=ax.transAxes,closed=True,
        facecolor=style['face'],edgecolor=style['edge'],linewidth=.45,
        joinstyle='round',zorder=8,clip_on=True)
    ax.add_patch(patch)
    text=ax.text(text_x,yy,f'C{channel}',transform=ax.transAxes,
        fontsize=size,fontfamily=BADGE_FONT,fontweight='normal',
        ha='center',va='center',color=style['text'],zorder=9,clip_on=True)
    if label_shift_div:
        anchor=(reference-ax.get_ylim()[0])/(ax.get_ylim()[1]-ax.get_ylim()[0])
        end=xx+ww if side=='left' else 1-xx-ww
        leader,=ax.plot([end,end+.015 if side=='left' else end-.015],[yy,anchor],
            transform=ax.transAxes,color=style['edge'],lw=.45,zorder=8,clip_on=True)
        return patch,text,leader
    return patch,text


def badge_shifts(settings):
    """Separate labels sharing a reference row; the waveform references stay fixed."""
    groups={}
    for ch,s in settings.items():groups.setdefault(round(s['position'],6),[]).append(ch)
    return {ch:(len(channels)-1-2*i)*.27
            for channels in groups.values() for i,ch in enumerate(channels)}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def commands(raw):
    result = {}
    for token in re.split(r'[;\r\n]+', raw.decode('latin1')):
        key, _, value = token.strip().partition(' ')
        result[key.upper()] = value
    return result

def font(size):
    from matplotlib.font_manager import findfont, FontProperties
    return ImageFont.truetype(findfont(FontProperties(family='DejaVu Sans')), size)

def channel_number(text):
    value=text.upper().removeprefix('CH').removeprefix('C')
    if not value.isdigit() or not 1<=int(value)<=8:
        raise argparse.ArgumentTypeError(f'invalid channel: {text}')
    return int(value)

def channel_value(text):
    key,sep,value=text.partition('=')
    if not sep:raise argparse.ArgumentTypeError('use CH1=2, for example')
    ch=channel_number(key)
    try: number=float(value)
    except ValueError:raise argparse.ArgumentTypeError(f'not a number: {value}')
    if not np.isfinite(number):raise argparse.ArgumentTypeError('value must be finite')
    return ch,number

def board(panels, destination, columns=2):
    width, height, header = 876, 476, 47
    canvas = Image.new('RGB',(width*columns,(height+header)*int(np.ceil(len(panels)/columns))),'#f1f2f3')
    draw = ImageDraw.Draw(canvas)
    for i,(name,path) in enumerate(panels):
        x,y=(i%columns)*width,(i//columns)*(height+header)
        draw.text((x+14,y+12),name,font=font(21),fill='#202020')
        with Image.open(path) as im:
            canvas.paste(im.convert('RGB').resize((width,height),Image.Resampling.LANCZOS),(x,y+header))
    canvas.save(destination)

def render(archive, settings, center, per_div, stem, out, *,
           crop_x_div=(0.,10.), crop_y_div=(-5.,5.), save_full=False):
    # Always construct the same native ten-by-ten pane, then crop its viewport.
    crop_x_div,crop_y_div=validate_crop(crop_x_div,crop_y_div)
    start,end=center-5*per_div,center+5*per_div
    fig=plt.figure(figsize=(8.76,4.76),dpi=200,facecolor='white')
    ax=fig.add_axes([0,0,1,1])
    grid=configure_scope_axes(ax,(0,10),1.,(-5,5),color='#777777',linewidth=.45)
    meta={'center_from_record_start_s':center,'time_per_div_s':per_div,
          'window_start_s':start,'window_end_s':end,'channels':{},
          'native_grid_divisions':[10,10],
          'crop_x_div':list(crop_x_div),'crop_y_div':list(crop_y_div),
          'visible_grid_divisions':[crop_x_div[1]-crop_x_div[0],crop_y_div[1]-crop_y_div[0]],
          'visible_window_start_s':start+crop_x_div[0]*per_div,
          'visible_window_end_s':start+crop_x_div[1]*per_div,
          'badge_font':BADGE_FONT,'badge_styles':BADGE_STYLES,
          'time_reference':'sample 0 is record start; trigger time = record time + WFM t0',
          'mapping':'display_div = (measured_value * gain - channel_OFFSET)/display_SCALE + display_POSITION; gain defaults to 1',
          'processing':'all stored samples in the full native window; crop changes only the viewport; no filtering, fitting or sample dropping'}
    badge_artists=[]; shifts=badge_shifts(settings)
    for ch,s in settings.items():
        w=archive.read_channel(ch).trace
        if w.extra.get('number_of_frames',0):
            raise ValueError('FastFrame data needs a frame selection')
        if start<0 or end>(w.record_length-1)*w.dt:
            raise ValueError(f'CH{ch}: window outside available sample times')
        lo=max(0,int(np.floor(start/w.dt)));hi=min(w.record_length,int(np.ceil(end/w.dt))+1)
        t=np.arange(lo,hi)*w.dt;v=w.values[lo:hi]
        x=(t-start)/per_div
        gain=s.get('gain',1.)
        y=(v*gain-s['offset'])/s['scale']+s['position']
        line,=ax.plot(x,y,color=COLORS.get(ch,'#444444'),lw=.60,solid_capstyle='butt',zorder=2)
        line.set_gid(f'CH{ch}-measured-samples')
        err=float(np.max(np.abs(((y-s['position'])*s['scale']+s['offset'])/gain-v)))
        assert err<1e-9
        meta['channels'][str(ch)]={**s,'units':w.units,'dt_s':w.dt,'sample_rate_Hz':w.sample_rate,
            'record_points':w.record_length,'record_start_relative_to_trigger_s':w.t0,
            'first_sample_index':lo,'last_sample_index':hi-1,'samples_drawn':hi-lo,
            'display_roundtrip_max_error_physical_units':err}
        drawn=draw_channel_badge(ax,ch,s['position'],label_shift_div=shifts[ch])
        if drawn:badge_artists.extend(drawn)
    if 1 in settings:
        y=settings[1]['position']
        ax.add_patch(Polygon([[9.75,y],[9.9,y+.20],[9.9,y+.04],[10,y+.04],
                              [10,y-.04],[9.9,y-.04],[9.9,y-.20]],
                             closed=True,facecolor='#ff0000',edgecolor='none',zorder=6))
    if save_full:
        for ext in ['png','svg']:
            fig.savefig(out/f'{stem}_full10x10.{ext}',dpi=200,facecolor='white',pad_inches=0)
    # Crop the same vector canvas. Keep mm/div fixed, so an 8x5 crop is 80% x 50%.
    ax.set_xlim(*crop_x_div);ax.set_ylim(*crop_y_div)
    fig.set_size_inches(8.76*(crop_x_div[1]-crop_x_div[0])/10,
                        4.76*(crop_y_div[1]-crop_y_div[0])/10)
    # Badge width and height were defined in axes coordinates before cropping.
    # Re-anchor badges on the cropped pane at their original native physical size.
    for artist in badge_artists:artist.remove()
    for ch,s in settings.items():
        draw_channel_badge(ax,ch,s['position'],width_mm=8.76*25.4*.04,
                           height_mm=4.76*25.4*.05,label_shift_div=shifts[ch])
    for ext in ['png','svg']:
        fig.savefig(out/f'{stem}.{ext}',dpi=200,facecolor='white',pad_inches=0)
    plt.close(fig)
    (out/f'{stem}.json').write_text(json.dumps(meta,indent=2)+'\n')
    return out/f'{stem}.png',meta

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='TSS path (default: reference fixed-180 record)')
    p.add_argument('--center-s',type=float,help='window center in seconds from sample 0')
    p.add_argument('--time-div-ms',type=float,default=5.,help='ms/div; total width = 10 divisions')
    p.add_argument('--output',type=Path,default=ROOT/'results')
    p.add_argument('--name',default='custom')
    p.add_argument('--reader-root',type=Path,default=READER)
    p.add_argument('--display-config',type=Path,help='JSON mapping channel number to scale/position/offset')
    p.add_argument('--scale',action='append',type=channel_value,default=[],metavar='CH=VALUE',help='repeatable physical units/div, e.g. CH1=2 (A/div for current)')
    p.add_argument('--offset',action='append',type=channel_value,default=[],metavar='CH=VALUE',help='repeatable physical offset in stored units; positive value moves trace down')
    p.add_argument('--position',action='append',type=channel_value,default=[],metavar='CH=DIV',help='repeatable screen position in div; positive moves trace up')
    p.add_argument('--gain',action='append',type=channel_value,default=[],metavar='CH=VALUE',help='repeatable confirmed probe correction, e.g. CH8=10; scale/offset then use corrected units')
    p.add_argument('--channels',help='visible channels in draw order, e.g. 1,2,5,6,7,8')
    p.add_argument('--crop-x-div',nargs=2,type=float,default=(0.,10.),metavar=('LEFT','RIGHT'),help='crop within the original x=0..10 div; e.g. 1 9 keeps eight divisions')
    p.add_argument('--crop-y-div',nargs=2,type=float,default=(-5.,5.),metavar=('BOTTOM','TOP'),help='crop within original y=-5..5 div; e.g. -3 2 keeps five divisions')
    p.add_argument('--save-full',action='store_true',help='also save the complete native 10x10 pane before cropping')
    p.add_argument('--demo',action='store_true',help='reference comparison plus shifted and zoomed windows')
    args=p.parse_args()
    try:validate_crop(args.crop_x_div,args.crop_y_div)
    except ValueError as e:p.error(str(e))
    if args.demo and (tuple(args.crop_x_div)!=(0.,10.) or tuple(args.crop_y_div)!=(-5.,5.)):
        p.error('--demo uses full panes; use normal mode for crops')
    if not np.isfinite(args.time_div_ms) or args.time_div_ms<=0: p.error('time-div-ms must be finite and positive')
    if args.center_s is not None and not np.isfinite(args.center_s):p.error('center-s must be finite')
    if Path(args.name).name!=args.name:p.error('name must be a filename stem')
    if args.demo and (args.center_s is not None or args.time_div_ms!=5.):
        p.error('--demo uses four fixed windows; use normal mode to choose a center and time/div')
    sys.path.insert(0,str(args.reader_root))
    from tsstools.tss import TssArchive
    ref=json.loads((DISPLAY_ROOT/'reference_case.json').read_text()) if not args.source else None
    source=(args.source or Path(ref['source'])).expanduser().resolve()
    source_hash=sha(source)
    if not args.source: assert source_hash==ref['source_sha256']
    a=TssArchive.open(source);setup=commands(a.setup_bytes)
    settings={};available={}
    for ch in a.channel_indices():
        state=setup.get(f':DISPLAY:WAVEVIEW1:CH{ch}:STATE','1')
        available[ch]={'scale':float(setup.get(f':DISPLAY:WAVEVIEW1:CH{ch}:VERTICAL:SCALE',setup[f':CH{ch}:SCALE'])),
                      'position':float(setup.get(f':DISPLAY:WAVEVIEW1:CH{ch}:VERTICAL:POSITION',setup[f':CH{ch}:POSITION'])),
                      'offset':float(setup[f':CH{ch}:OFFSET'])}
        if state=='1':settings[ch]=available[ch].copy()
    # The reference PNG was exported later with revised display settings. Those
    # settings are traced to its export plan, not claimed to be in the source TSS.
    if not args.source:
        for ch,value in ref['new_positions_div'].items():settings[int(ch)]['position']=value
        for ch,value in ref['new_scales_A_per_div'].items():settings[int(ch)]['scale']=value
        for ch,value in ref['display_states'].items():
            if not value:settings.pop(int(ch),None)
    if args.display_config:
        settings={int(k):v for k,v in json.loads(args.display_config.read_text()).items()}
    if args.channels:
        selected=[channel_number(c.strip()) for c in args.channels.split(',')]
        if len(set(selected))!=len(selected):p.error('duplicate channel in --channels')
        if set(selected)-set(available):p.error('selected channel absent from TSS')
        settings={ch:settings.get(ch,available[ch]).copy() for ch in selected}
    for key,overrides in [('scale',args.scale),('offset',args.offset),('position',args.position),('gain',args.gain)]:
        for ch,value in overrides:
            if ch not in settings:p.error(f'CH{ch} is not selected; include it in --channels or display-config')
            settings[ch][key]=value
    if not settings or set(settings)-set(available):p.error('need channels present in TSS')
    for ch,s in settings.items():
        if not {'scale','offset','position'}<=set(s) or set(s)-{'scale','offset','position','gain'} or not all(np.isfinite(v) for v in s.values()) or s['scale']<=0 or s.get('gain',1)<=0:
            p.error(f'CH{ch}: need finite scale > 0, gain > 0, offset and position')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'display_config.json').write_text(json.dumps(settings,indent=2)+'\n')
    if args.demo:
        if args.source:raise ValueError('--demo uses only the matched reference source')
        reference=DISPLAY_ROOT/'original_scope.png'
        Image.open(reference).crop((0,520,876,996)).save(out/'original_crop.png')
        ref_center=ref['center_from_record_start_s']
        drawings=[];metadata=[]
        for name,c,d in [('redraw_5ms',ref_center,.005),('shifted_5ms',ref_center+.015,.005),
                         ('zoom_1ms',2.499,.001),('samples_20us',2.490,.000020)]:
            im,m=render(a,settings,c,d,name,out);drawings.append(im);metadata.append(m)
        board([('Original scope export | 5 ms/div',out/'original_crop.png'),
               ('Measured samples redrawn | 5 ms/div',drawings[0])],out/'comparison.png')
        board([('Same window | 5 ms/div',drawings[0]),('Center shifted +15 ms | 5 ms/div',drawings[1]),
               ('Transient detail | 1 ms/div',drawings[2]),('Stored samples | 20 us/div',drawings[3])],out/'window_examples.png')
        manifest={'source_tss':str(source),'source_sha256':source_hash,
                  'original_scope_export':str(reference),'original_scope_export_sha256':sha(reference),
                  'display_plan':str(DISPLAY_ROOT/'export_plan.json'),'display_plan_sha256':sha(DISPLAY_ROOT/'export_plan.json'),
                  'reference_crop_xyxy':[0,520,876,996],'source_unchanged':sha(source)==source_hash,
                  'reference_is':'previous genuine hardware PNG of this same TSS, with display settings overridden in the export plan',
                  'rendered_from':'WFM samples + source setup + documented display overrides; no screenshot pixels used in redraw',
                  'windows':metadata}
    else:
        center=args.center_s
        if center is None:
            w=a.read_channel(next(iter(settings))).trace
            center=ref['center_from_record_start_s'] if ref else w.record_length*w.dt/2
        _,m=render(a,settings,center,args.time_div_ms/1000,args.name,out,
                   crop_x_div=args.crop_x_div,crop_y_div=args.crop_y_div,save_full=args.save_full)
        manifest={'source_tss':str(source),'source_sha256':source_hash,'source_unchanged':sha(source)==source_hash,'windows':[m]}
    manifest['renderer_sha256']=sha(__file__)
    manifest['outputs']={p.name:sha(p) for p in sorted(out.iterdir()) if p.suffix in {'.png','.svg','.json'} and p.name!='manifest.json'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'output':str(out),'source_unchanged':manifest['source_unchanged'],'windows':len(manifest['windows'])}))

if __name__=='__main__':main()
