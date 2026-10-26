"""Original, deterministic motion graphics streamed through Pillow into FFmpeg.

No stock footage or billed image/video calls are made here. Optional source images
are truthfully labelled as still images with local pan/zoom; diagram objects,
curves, packets, camera and parallax are animated independently on every frame.
"""
from __future__ import annotations

import json
import math
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageOps

from .caption_engine import CaptionCue, build_caption_cues, display_text, safe_box, text_layout, write_captions
from .audio_edit import prepare_narration
from .render import _font, _run, media_duration, require_ffmpeg
from .story import Story


PRESETS = {
    "explainer": {"top": (8, 26, 42), "bottom": (4, 12, 24), "accent": (77, 226, 206), "warm": (255, 195, 85),
                  "muted": (64, 112, 137), "land": (33, 102, 114), "ink": (235, 248, 250)},
    "cinematic": {"top": (5, 12, 26), "bottom": (2, 5, 15), "accent": (79, 197, 245), "warm": (255, 187, 99),
                  "muted": (51, 71, 103), "land": (36, 74, 90), "ink": (239, 244, 252)},
}
CABLE_KINDS = ["globe_dive", "fiber_pulse", "network_flow", "cable_reroute", "route_compare", "globe_reveal"]
ALIASES = {"world": "globe_dive", "globe": "globe_dive", "ocean": "fiber_pulse", "fiber": "fiber_detail",
           "packets": "network_flow", "reroute": "cable_reroute", "satellite": "globe_reveal",
           "tire_rotate": "tire", "rubber_flex": "tire_tread", "contact_patch": "tire", "sipe_edges": "tire_tread", "tire_reveal": "tire",
           "space_flash": "sound", "sound_wave": "sound", "vacuum_gap": "sound", "radio_signal": "sound", "space_reveal": "sound"}


@lru_cache(maxsize=64)
def _cached_font(size: int):
    return _font(size)


def _ease(value: float) -> float:
    value = max(0., min(1., value))
    return value * value * (3 - 2 * value)


def _mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _point_curve(points: list[tuple[float, float]], t: float) -> tuple[float, float]:
    # Cubic Bezier control points. Paths and packet positions share one function.
    a, b, c, d = points
    return ((1-t)**3*a[0] + 3*(1-t)**2*t*b[0] + 3*(1-t)*t*t*c[0] + t**3*d[0],
            (1-t)**3*a[1] + 3*(1-t)**2*t*b[1] + 3*(1-t)*t*t*c[1] + t**3*d[1])


def _background(width: int, height: int, palette: dict) -> Image.Image:
    strip = Image.new("RGB", (1, height))
    strip.putdata([_mix(palette["top"], palette["bottom"], y/max(1, height-1)) for y in range(height)])
    base = strip.resize((width, height))
    draw = ImageDraw.Draw(base)
    # Quiet navigation grid acts as a spatial reference during camera moves.
    step = max(18, width // 10)
    for x in range(-step, width + step, step):
        draw.line((x, height*.28, x + width*.15, height*.68), fill=_mix(palette["top"], palette["muted"], .11), width=1)
    for y in range(round(height*.28), round(height*.69), step):
        draw.line((0, y, width, y), fill=_mix(palette["top"], palette["muted"], .11), width=1)
    return base


class MotionScene:
    """One scene's cached artwork and text geometry, evaluated continuously in time."""

    def __init__(self, story: Story, index: int, duration: float, width: int, height: int,
                 style: str, kind: str, cues: list[CaptionCue], image: Path | None = None):
        self.story, self.index, self.duration = story, index, duration
        self.width, self.height = width, height
        self.palette = PRESETS[style]
        self.style, self.kind = style, ALIASES.get(kind, kind)
        self.cues = cues
        self.offset = cues[0].start if cues else 0.
        # Imported timings may start after scene start. Caller sets actual offset.
        self.scene_offset = 0.
        self.base = _background(width, height, self.palette)
        self.image = None
        if image is not None:
            with Image.open(image) as source:
                self.image = ImageOps.fit(source.convert("RGB"), (round(width*1.14), round(height*1.14)), method=Image.Resampling.LANCZOS)
        scratch = ImageDraw.Draw(self.base)
        label_box = (round(width*.08), round(height*.10), round(width*.85), round(height*.14))
        title_box = (round(width*.08), round(height*.155), round(width*.85), round(height*.255))
        self.brand = text_layout(scratch, "НЕВИДИМЫЙ МИР", width=width, height=height, center_y=height*.115,
                                 font_fraction=.027, max_lines=1, box=label_box)
        self.title = text_layout(scratch, story.scenes[index].caption, width=width, height=height, center_y=height*.205,
                                 font_fraction=.070, max_lines=2, box=title_box)
        self.caption_layouts = {cue.text: text_layout(scratch, cue.text, width=width, height=height,
                                  center_y=height*.754, font_fraction=.061, max_lines=2,
                                  box=(round(width*.08), round(height*.705), round(width*.85), round(height*.795))) for cue in cues}
        self.text_geometries = [{"role": "channel", **self.brand}, {"role": "scene_heading", **self.title}]
        self.text_geometries += [{"role": "caption", "text": cue.text, "start": cue.start, "end": cue.end,
                                  **self.caption_layouts[cue.text]} for cue in cues]
        self.disclosure = self.disclosure_layout()
        self.text_geometries.append({"role": "diagram_disclosure", **self.disclosure})

    def disclosure_layout(self) -> dict:
        return text_layout(ImageDraw.Draw(self.base), "Схематичная иллюстрация", width=self.width, height=self.height,
                           center_y=self.height*.671, font_fraction=.026, max_lines=1,
                           box=(round(self.width*.08), round(self.height*.655), round(self.width*.85), round(self.height*.69)))

    def xy(self, x: float, y: float, *, camera: float = 1.) -> tuple[int, int]:
        # Diagram coordinates stay between captions and headline. Scale is a
        # deliberate small camera push, never jitter or random scene motion.
        return round(self.width * (.465 + (x - .465)*camera)), round(self.height * (.465 + (y - .465)*camera))

    def line(self, draw, points: Iterable[tuple[float, float]], color, width: float = .004, *, camera: float = 1.):
        coords = [self.xy(x, y, camera=camera) for x, y in points]
        if len(coords) >= 2:
            draw.line(coords, fill=color, width=max(1, round(self.width*width)), joint="curve")

    def curve(self, draw, controls: list[tuple[float, float]], color, width: float = .005,
              reveal: float = 1., glow: bool = False, camera: float = 1.):
        count = max(2, round(90 * max(.01, min(1., reveal))))
        points = [_point_curve(controls, n/90) for n in range(count + 1)]
        if glow:
            self.line(draw, points, _mix(self.palette["top"], color, .19), width*4.5, camera=camera)
            self.line(draw, points, _mix(self.palette["top"], color, .40), width*2.0, camera=camera)
        self.line(draw, points, color, width, camera=camera)

    def dot(self, draw, x: float, y: float, radius: float, color, *, glow: bool = False, camera: float = 1.):
        px, py = self.xy(x, y, camera=camera)
        r = max(1, round(self.width*radius*camera))
        if glow:
            for factor, strength in ((3.1, .10), (2., .25)):
                rr = round(r*factor)
                draw.ellipse((px-rr, py-rr, px+rr, py+rr), fill=_mix(self.palette["top"], color, strength))
        draw.ellipse((px-r, py-r, px+r, py+r), fill=color)

    def packets(self, draw, controls, time: float, *, speed: float = .21, count: int = 5,
                color=None, limit: float = 1., camera: float = 1.):
        color = color or self.palette["warm"]
        for index in range(count):
            q = (time * speed + index/count) % 1
            if q > limit:
                continue
            x, y = _point_curve(controls, q)
            # Distinct short tail makes direction visible even on a small phone.
            tail = [_point_curve(controls, max(0., q - k*.012)) for k in range(4)]
            self.line(draw, tail, _mix(self.palette["top"], color, .45), .006, camera=camera)
            self.dot(draw, x, y, .0065, color, glow=True, camera=camera)

    def _satellite(self, draw, x, y, time, *, camera=1.):
        c = self.palette
        center = self.xy(x, y, camera=camera)
        w = self.width*.025
        draw.rectangle((center[0]-w*.5, center[1]-w*.7, center[0]+w*.5, center[1]+w*.7), fill=c["ink"])
        for sign in (-1, 1):
            draw.rectangle((center[0]+sign*w*.8-w*.65, center[1]-w*.55,
                            center[0]+sign*w*.8+w*.65, center[1]+w*.55), fill=c["muted"], outline=c["accent"], width=max(1, round(w*.08)))
        self.line(draw, [(x, y+.015), (x+.017, y+.028)], c["ink"], .002, camera=camera)
        # Restrained signalling arcs expand once per second, without flashing.
        q = (time*.4) % 1
        alpha = max(0., 1-q)
        col = _mix(c["top"], c["accent"], alpha*.45)
        self.curve(draw, [(x-.015, y+.035+q*.025), (x-.005, y+.05+q*.025), (x+.02, y+.05+q*.025), (x+.03, y+.035+q*.025)], col, .0015, camera=camera)

    def _globe(self, draw, time: float, progress: float, *, reveal: bool = False):
        c = self.palette
        camera = 1 + .045 * _ease(progress)
        center_x, center_y, radius = .465, .475, .315
        cx, cy = self.xy(center_x, center_y, camera=camera)
        r = round(self.width*radius*camera)
        # Layered limb illumination is original raster geometry, not simulated 3D.
        for extra in (10, 7, 4):
            rr = r + max(1, round(self.width*extra/300))
            draw.ellipse((cx-rr, cy-rr, cx+rr, cy+rr), fill=_mix(c["top"], c["accent"], .025*(11-extra)))
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(9, 40, 61), outline=c["muted"], width=max(1, round(self.width*.003)))
        # Project low-detail geographic silhouettes on a sphere. Lines are
        # illustrative network routes; there is no claim of real cable mapping.
        land_polygons = [
            [(-78, 14),(-115,30),(-125,49),(-100,60),(-69,48),(-52,45),(-67,27)],
            [(-73,10),(-44,-5),(-38,-20),(-62,-55),(-77,-13)],
            [(-14,36),(20,36),(43,10),(31,-32),(14,-36),(-10,4)],
            [(-9,38),(4,58),(40,66),(75,56),(110,60),(140,44),(118,20),(83,8),(56,26),(29,40)],
            [(113,-15),(142,-10),(154,-30),(130,-41),(113,-27)],
        ]
        rotation = (-15 if reveal else -28) + time*2.8
        def projection(lon, lat):
            lo, la = math.radians(lon-rotation), math.radians(lat)
            return cx + r*math.cos(la)*math.sin(lo), cy - r*math.sin(la), math.cos(la)*math.cos(lo)
        for poly in land_polygons:
            projected = [projection(lon, lat) for lon, lat in poly]
            if all(z > -.15 for _, _, z in projected):
                draw.polygon([(round(x), round(y)) for x,y,_ in projected], fill=c["land"])
        for meridian in (-.65, 0., .65):
            rx = r * max(.10, abs(meridian))
            draw.ellipse((cx-rx, cy-r, cx+rx, cy+r), outline=(31, 69, 87), width=max(1, round(self.width*.0015)))
        for latitude in (-.45, .45):
            yy = cy + latitude*r
            rx = r*math.sqrt(1-latitude*latitude)
            draw.arc((cx-rx, yy-r*.13, cx+rx, yy+r*.13), 0, 180, fill=(31, 69, 87), width=max(1, round(self.width*.0015)))
        routes = [
            [(.23,.447),(.33,.39),(.48,.405),(.61,.443)],
            [(.25,.455),(.35,.51),(.51,.53),(.66,.49)],
            [(.37,.54),(.43,.57),(.58,.58),(.70,.515)],
            [(.29,.42),(.37,.38),(.49,.37),(.57,.42)],
        ]
        for index, path in enumerate(routes):
            amount = _ease((progress-.06-index*.06)*2.5) if reveal else _ease(progress*2.5)
            self.curve(draw, path, c["accent"], .003, reveal=amount, glow=True, camera=camera)
            if amount > .9:
                self.packets(draw, path, time+index*.23, count=3, camera=camera)
        self._satellite(draw, .70-math.sin(time*.18)*.035, .303+math.cos(time*.18)*.008, time, camera=camera)
        if not reveal:
            # Dive cue: descending light path toward the undersea destination.
            dive = [(.67,.36),(.64,.39),(.55,.51),(.48,.57)]
            self.curve(draw, dive, _mix(c["top"], c["warm"], .65), .002, reveal=_ease((progress-.35)*2), camera=camera)
        for x,y in ((.23,.447),(.61,.443),(.37,.54),(.70,.515)):
            self.dot(draw, x,y,.006,c["warm"], camera=camera)

    def _ocean(self, draw, time: float, progress: float):
        c = self.palette
        camera = 1 + .055*_ease(progress)
        # Coast-to-coast cross section with multiple independently moving depths.
        water_top = .358
        water = [self.xy(-.1,water_top), self.xy(1.1,water_top), self.xy(1.1,.637), self.xy(-.1,.637)]
        draw.polygon(water, fill=(6, 35, 57))
        for layer in range(5):
            y = water_top + layer*.045
            col = _mix((9,48,72),(5,17,33), layer/5)
            points = [(i/50, y+.004*math.sin(i*.4+time*.5+layer)) for i in range(-5,56)]
            draw.polygon([self.xy(*points[0]), *[self.xy(x,yy) for x,yy in points], self.xy(1.1,.645), self.xy(-.1,.645)], fill=col)
        coast_left = [(-.1,.34),(.16,.34),(.19,.42),(.25,.54),(.34,.58),(.50,.59),(.68,.56),(.76,.43),(.80,.34),(1.1,.34),(1.1,.66),(-.1,.66)]
        draw.polygon([self.xy(x,y,camera=camera) for x,y in coast_left], fill=(27,47,60))
        self.line(draw, coast_left[:10], c["muted"], .005, camera=camera)
        # Buildings at cable landing sites.
        for x, h in ((.12,.032),(.17,.022),(.79,.025),(.84,.04)):
            p1,p2 = self.xy(x,.34-h,camera=camera),self.xy(x+.025,.34,camera=camera)
            draw.rectangle((*p1,*p2), fill=c["land"], outline=c["accent"], width=max(1, round(self.width*.002)))
        path = [(.17,.35),(.27,.64),(.63,.65),(.81,.35)]
        self.curve(draw, path, c["warm"], .009, reveal=_ease(progress*3), glow=True, camera=camera)
        self.packets(draw, path, time, count=7, speed=.17, camera=camera)
        # Suspended particles have slower near/far parallax than the data stream.
        for i in range(25):
            x = (.19+(i*.137)% .62 + .008*math.sin(time*.13+i))
            y = .38 + (i*.037+time*.001*(1+i%3))% .19
            self.dot(draw,x,y,.0017,_mix(c["top"],c["accent"],.24),camera=camera)
        # A late optical-fibre cutaway gives a new visual beat rather than a card.
        if progress > .42:
            alpha = _ease((progress-.42)*4)
            x,y = self.xy(.46,.425,camera=camera)
            r = round(self.width*.072*alpha)
            if r > 1:
                draw.ellipse((x-r,y-r,x+r,y+r), fill=(5,16,28), outline=c["warm"], width=max(1, round(self.width*.003)))
                for i in range(7):
                    angle = i*math.tau/7+time*.12
                    self.dot(draw,.46+math.cos(angle)*.037*alpha,.425+math.sin(angle)*.021*alpha,.006,c["accent"],glow=True,camera=camera)

    def _fiber(self, draw, time, progress):
        c = self.palette
        for i in range(9):
            spread = (i-4)*.018
            path = [(.14,.46+spread*.1),(.35,.46+spread*.15),(.5,.46+spread),(.81,.46+spread)]
            color = _mix(c["accent"],c["warm"],i/8)
            self.curve(draw,path,color,.004,glow=True,reveal=_ease(progress*3))
            self.packets(draw,path,time+i*.07,count=3,color=color)
        a,b = self.xy(.13,.405),self.xy(.30,.515)
        draw.rounded_rectangle((*a,*b), radius=max(2,round(self.width*.027)), fill=(25,42,56),outline=c["muted"],width=max(1,round(self.width*.005)))
        for i in range(7):
            y = .425+i*.012
            self.line(draw,[(.14,y),(.3,y)],_mix(c["muted"],c["accent"],.2),.002)

    def _network(self, draw, time: float, progress: float, *, broken: bool = False, compare: bool = False):
        c = self.palette
        camera = 1+.035*_ease(progress)
        nodes = [( .17,.48),(.38,.375),(.62,.375),(.78,.48),(.38,.585),(.62,.585)]
        main = [(.17,.48),(.33,.42),(.61,.42),(.78,.48)]
        alternate = [(.17,.48),(.34,.645),(.62,.645),(.78,.48)]
        damage = broken and progress > .28
        for i in (1,2,4,5):
            x,y = nodes[i]
            left = [nodes[0],(.22,y),(.29,y),(x,y)]
            right = [(x,y),(.68,y),(.73,y),nodes[3]]
            self.curve(draw,left,_mix(c["top"],c["muted"],.55),.002,camera=camera)
            self.curve(draw,right,_mix(c["top"],c["muted"],.55),.002,camera=camera)
        if compare:
            damage = True
        if damage:
            self.curve(draw,main,_mix(c["top"],(227,103,102),.6),.006,camera=camera)
            # Erase a visible gap in the broken cable, then draw a restrained X.
            self.line(draw,[(.446,.435),(.50,.435)],c["top"],.027,camera=camera)
            self.line(draw,[(.45,.422),(.49,.447)],(239,119,108),.004,camera=camera)
            self.line(draw,[(.49,.422),(.45,.447)],(239,119,108),.004,camera=camera)
            if not compare:
                self.packets(draw,main,time,count=4,limit=.44,camera=camera)
            restore = _ease((progress-.48)*3) if not compare else _ease(progress*3)
            alternate_color=_mix(c["muted"],c["warm"],.40) if compare else c["accent"]
            self.curve(draw,alternate,alternate_color,.0025 if compare else .005,reveal=restore,glow=not compare,camera=camera)
            if restore > .9:
                self.packets(draw,alternate,time,speed=.10 if compare else .25,count=2 if compare else 5,
                             color=_mix(c["warm"],c["muted"],.25) if compare else None,camera=camera)
            if compare:
                # One endpoint has no spare route: visual limitation, not a
                # fictional numeric speed/availability claim.
                lone = [(.62,.375),(.71,.35),(.8,.35),(.83,.385)]
                self.curve(draw,lone,c["muted"],.003,camera=camera)
                self.dot(draw,.83,.385,.012,(203,121,108),camera=camera)
                self.line(draw,[(.815,.365),(.845,.405)],(239,119,108),.003,camera=camera)
                # Narrow gate makes the limited spare capacity legible. Sparse
                # amber flow is a qualitative illustration, not a speed metric.
                for x in (.44,.50):
                    self.line(draw,[(x,.580),(x,.625)],c["warm"],.004,camera=camera)
        else:
            self.curve(draw,main,c["accent"],.006,reveal=_ease(progress*4),glow=True,camera=camera)
            self.packets(draw,main,time,count=6,camera=camera)
            if progress > .35:
                self.curve(draw,alternate,c["muted"],.003,reveal=_ease((progress-.35)*3),camera=camera)
                self.packets(draw,alternate,time,speed=.15,count=4,color=c["accent"],camera=camera)
        for index,(x,y) in enumerate(nodes):
            node_color=(203,121,108) if compare and index==3 else (c["muted"] if compare and index in (1,2) else c["accent"])
            self.dot(draw,x,y,.013 if index in (0,3) else .009,node_color,glow=not compare or index==0,camera=camera)
            self.dot(draw,x,y,.004,c["muted"] if compare and index==3 else c["ink"],camera=camera)
        # Message/video/call pictograms become visible in distinct beats.
        if not broken and not compare:
            for i,x in enumerate((.28,.465,.65)):
                if progress < .17+i*.16:
                    continue
                px,py = self.xy(x,.315,camera=camera)
                r = self.width*.025
                draw.rounded_rectangle((px-r,py-r*.7,px+r,py+r*.7),radius=max(1,round(r*.18)),outline=c["warm"],width=max(1,round(self.width*.003)))
                if i == 1:
                    draw.polygon([(px-r*.25,py-r*.4),(px-r*.25,py+r*.4),(px+r*.4,py)],fill=c["warm"])
                elif i == 0:
                    self.line(draw,[(x-.014,.315),(x+.014,.315)],c["warm"],.002,camera=camera)
                else:
                    draw.arc((px-r*.4,py-r*.45,px+r*.4,py+r*.45),45,285,fill=c["warm"],width=max(1,round(self.width*.004)))

    def _airflow(self, draw, time, progress):
        c = self.palette
        camera = 1+.05*_ease(progress)
        # Side-view wing: animated flow is explanatory geometry, not CFD output.
        wing = [(.17,.49),(.35,.44),(.60,.47),(.81,.51),(.37,.51)]
        for i in range(8):
            y = .36+i*.035
            lift = -.035 if y < .49 else .012
            curve = [(.10,y),(.30,y+lift),(.62,y+lift),(.86,y)]
            self.curve(draw,curve,_mix(c["top"],c["accent"],.45),.002,camera=camera)
            self.packets(draw,curve,time,count=3,speed=.22,color=c["accent"],camera=camera)
        draw.polygon([self.xy(x,y,camera=camera) for x,y in wing],fill=c["land"])
        self.line(draw,wing+[wing[0]],c["warm"],.004,camera=camera)
        # Gliding trajectory appears after first flow explanation.
        if progress > .4:
            trajectory = [(.17,.38),(.32,.40),(.68,.54),(.81,.56)]
            self.curve(draw,trajectory,c["warm"],.003,reveal=_ease((progress-.4)*3),camera=camera)

    def _power(self, draw, time, progress):
        c = self.palette
        body_a,body_b = self.xy(.29,.355),self.xy(.64,.585)
        radius = max(2,round(self.width*.025))
        draw.rounded_rectangle((*body_a,*body_b),radius=radius,fill=(10,32,47),outline=c["muted"],width=max(1,round(self.width*.005)))
        a,b = self.xy(.40,.34),self.xy(.53,.355)
        draw.rectangle((*a,*b),fill=c["muted"])
        level = .78-.23*_ease(progress)
        a,b = self.xy(.32,.56-level*.18),self.xy(.61,.56)
        draw.rounded_rectangle((*a,*b),radius=max(1,radius//2),fill=_mix(c["land"],c["accent"],.52))
        for i in range(7):
            path = [(.1,.385+i*.03),(.22,.38+i*.03),(.74,.4+i*.02),(.83,.375+i*.03)]
            self.packets(draw,path,time,speed=.15*(1-.5*progress),count=3,color=c["warm"])
        # Snow crystal geometry is used only for cold/battery stories.
        for i in range(6):
            angle = i*math.tau/6+time*.06
            self.line(draw,[(.76,.35),(.76+math.cos(angle)*.038,.35+math.sin(angle)*.021)],c["accent"],.002)

    def _orbit(self, draw, time, progress):
        c=self.palette
        cx,cy=self.xy(.465,.48)
        r=round(self.width*.18)
        draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(7,34,53),outline=c["accent"],width=max(1,round(self.width*.004)))
        for i in range(3):
            angle=time*.35+i*math.tau/3
            x=.465+math.cos(angle)*.32
            y=.48+math.sin(angle)*.105
            self._satellite(draw,x,y,time)
            self.line(draw,[(x,y),(.465,.48)],_mix(c["top"],c["accent"],.4),.001)
        for i in range(20):
            x=(i*.1618)%1
            y=.3+(i*.0873)%.32
            self.dot(draw,x,y,.0015,c["muted"])
        if progress > .5:
            self.curve(draw,[(.17,.52),(.3,.64),(.65,.64),(.80,.52)],c["warm"],.003,reveal=_ease((progress-.5)*3))

    def _tire(self, draw, time, progress, *, tread=False):
        c=self.palette
        camera=1+.035*_ease(progress)
        if tread:
            # Plan view of the rolling tread. Grooves move independently while
            # small snow particles are displaced toward the sides of the patch.
            a,b=self.xy(.30,.32,camera=camera),self.xy(.63,.61,camera=camera)
            draw.rounded_rectangle((*a,*b),radius=max(2,round(self.width*.04)),fill=(23,37,50),outline=c["muted"],width=max(1,round(self.width*.004)))
            for i in range(10):
                y=.33+(i*.033+time*.025)%.26
                self.line(draw,[(.315,y),(.43,y+.020),(.46,y+.020)],c["accent"],.005,camera=camera)
                self.line(draw,[(.475,y+.020),(.50,y+.020),(.615,y)],c["accent"],.005,camera=camera)
                self.line(draw,[(.36,y+.018),(.36,y+.027)],c["ink"],.002,camera=camera)
                self.line(draw,[(.57,y+.018),(.57,y+.027)],c["ink"],.002,camera=camera)
            for i in range(12):
                side=-1 if i%2 else 1
                q=(time*.25+i*.13)%1
                self.dot(draw,.465+side*(.18+q*.11),.37+(i*.031)%.20,.003,c["ink"],camera=camera)
            a,b=self.xy(.28,.47,camera=camera),self.xy(.65,.53,camera=camera)
            draw.rounded_rectangle((*a,*b),radius=max(2,round(self.width*.01)),outline=c["warm"],width=max(1,round(self.width*.003)))
        else:
            cx,cy=self.xy(.465,.445,camera=camera)
            r=round(self.width*.205*camera)
            road_y=cy+r
            draw.rectangle((0,road_y,self.width,round(self.height*.635)),fill=(28,50,65))
            for i in range(21):
                x=(i*.071-time*.027)%1
                y=.574+(i*.019)%.055
                self.dot(draw,x,y,.0025,c["ink"],camera=camera)
            draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(10,18,29),outline=c["muted"],width=max(1,round(self.width*.005)))
            for i in range(26):
                angle=i*math.tau/26-time*.9
                x1,y1=cx+math.cos(angle)*r*.88,cy+math.sin(angle)*r*.88
                x2,y2=cx+math.cos(angle+.035)*r*.98,cy+math.sin(angle+.035)*r*.98
                draw.line((x1,y1,x2,y2),fill=c["muted"],width=max(1,round(self.width*.008)))
            inner=r*.57
            draw.ellipse((cx-inner,cy-inner,cx+inner,cy+inner),fill=(35,66,83),outline=c["accent"],width=max(1,round(self.width*.004)))
            for i in range(6):
                angle=i*math.tau/6-time*.9
                draw.line((cx,cy,cx+math.cos(angle)*inner*.83,cy+math.sin(angle)*inner*.83),fill=c["ink"],width=max(1,round(self.width*.008)))
            self.dot(draw,.465,.445,.022,c["warm"],camera=camera)
            patch=round(self.width*(.072+.004*math.sin(time*1.2)))
            draw.line((cx-patch,road_y-1,cx+patch,road_y-1),fill=c["warm"],width=max(2,round(self.width*.012)))
            # The contact patch is a schematic highlight, never a numerical
            # friction simulation or a fabricated stopping-distance claim.
            if progress>.35:
                for i in range(3):
                    x=.40+i*.066
                    self.line(draw,[(x,.613),(x,.59)],c["accent"],.003,camera=camera)
                    self.line(draw,[(x-.008,.598),(x,.59),(x+.008,.598)],c["accent"],.003,camera=camera)

    def _sound(self, draw, time, progress):
        c=self.palette
        phase=self.index%5
        if phase==3:
            # Electromagnetic transmission gets a continuous field schematic,
            # distinct from mechanical displacement of medium particles below.
            carrier=[(.15+i*.70/100,.47+.033*math.sin(i*.34-time*4)) for i in range(101)]
            self.line(draw,carrier,c["accent"],.003)
            self._satellite(draw,.16,.43,time)
            self.line(draw,[(.79,.43),(.76,.49),(.82,.49),(.79,.43)],c["warm"],.004)
            return
        gap=phase in (0,2,4)
        for i in range(22):
            base=.14+i*.032
            if gap and .40<base<.61:
                continue
            # In the vacuum demonstration the right cluster stays still: the
            # mechanical disturbance cannot jump the missing medium.
            moving=not gap or base<.40
            x=base+(.009*math.sin(i*.7-time*4) if moving else 0)
            for row in range(3):
                y=.435+row*.034
                self.dot(draw,x,y,.005,c["accent"] if moving else c["muted"],glow=moving and row==1)
        if gap:
            for y in (.405,.535):
                for i in range(5):
                    self.line(draw,[(.42+i*.034,y),(.437+i*.034,y)],c["muted"],.002)
            if phase==0:
                for i in range(8):
                    angle=i*math.tau/8+time*.04
                    q=_ease(min(1,progress*3))
                    self.line(draw,[(.22+math.cos(angle)*.035,.34+math.sin(angle)*.02),
                                    (.22+math.cos(angle)*(.04+.035*q),.34+math.sin(angle)*(.023+.02*q))],c["warm"],.004)
        else:
            # This overlay visualizes pressure variation while particles
            # oscillate locally; none flies from sender to receiver.
            wave=[(.14+i*.69/100,.57+.020*math.sin(i*.22-time*4)) for i in range(101)]
            self.line(draw,wave,c["warm"],.003)

    def _text(self, draw, layout: dict, color, *, shadow: bool = False):
        font=_cached_font(layout["font_size"])
        for row in layout["lines"]:
            draw.text((row["x"],row["y"]),row["text"],font=font,fill=color,anchor="lt",
                      stroke_width=max(1,round(self.width*.002)) if shadow else 0,stroke_fill=(3,10,18))

    def frame(self, time: float) -> Image.Image:
        progress = max(0., min(1., time/self.duration))
        c=self.palette
        if self.image is not None:
            # Real image provider preserved: this is a local motion treatment of
            # the supplied still, with procedural diagrams as a separate layer.
            scale=1+.045*_ease(progress)
            iw,ih=self.image.size
            crop_w,crop_h=round(self.width/scale),round(self.height/scale)
            x=round((iw-crop_w)*(.36+.16*progress))
            y=round((ih-crop_h)*(.45+.08*math.sin(progress*math.pi)))
            canvas=self.image.crop((x,y,x+crop_w,y+crop_h)).resize((self.width,self.height),Image.Resampling.BICUBIC)
            canvas=Image.blend(canvas,self.base,.73)
        else:
            canvas=self.base.copy()
        draw=ImageDraw.Draw(canvas)
        # Slow dust/parallax gives depth without decorative shaking.
        for i in range(16 if self.style=="cinematic" else 8):
            x=(i*.1787+time*.001*(1+i%3))%1
            y=.28+(i*.071)% .35
            self.dot(draw,x,y,.0015,_mix(c["top"],c["muted"],.45))
        if self.kind in {"globe_dive","globe_reveal"}:
            self._globe(draw,time,progress,reveal=self.kind=="globe_reveal")
        elif self.kind=="fiber_pulse":
            self._ocean(draw,time,progress)
        elif self.kind=="fiber_detail":
            self._fiber(draw,time,progress)
        elif self.kind in {"network_flow","cable_reroute","route_compare","network"}:
            self._network(draw,time,progress,broken=self.kind=="cable_reroute",compare=self.kind=="route_compare")
        elif self.kind=="airflow":
            self._airflow(draw,time,progress)
        elif self.kind=="power":
            self._power(draw,time,progress)
        elif self.kind=="orbit":
            self._orbit(draw,time,progress)
        elif self.kind in {"tire","tire_tread"}:
            self._tire(draw,time,progress,tread=self.kind=="tire_tread" or self.index%2==1)
        elif self.kind=="sound":
            self._sound(draw,time,progress)
        else:
            raise ValueError(f"Unsupported motion kind: {self.kind}")
        # Separate zones for heading, illustration and short narration phrase.
        self._text(draw,self.brand,c["accent"])
        self._text(draw,self.title,c["ink"],shadow=True)
        self._text(draw,self.disclosure,c["muted"])
        absolute=self.scene_offset+time
        cue=next((cue for cue in self.cues if cue.start <= absolute < cue.end),None)
        if cue:
            layout=self.caption_layouts[cue.text]
            l,t,r,b=layout["bounds"]
            pad=max(2,round(self.width*.014))
            draw.rounded_rectangle((l-pad,t-pad,r+pad,b+pad),radius=max(2,round(self.width*.012)),fill=(4,13,25))
            self._text(draw,layout,c["ink"],shadow=True)
            # Imported genuine word times can highlight current word. Fallback
            # never guesses a speaking word; its unit is the complete phrase.
            if cue.words:
                active_index=next((i for i,w in enumerate(cue.words) if w["start"] <= absolute < w["end"]),None)
                if active_index is not None:
                    font=_cached_font(layout["font_size"])
                    # Character offsets without whitespace survive line wrapping
                    # and correctly distinguish repeated words in one phrase.
                    active_start=sum(len(word["word"]) for word in cue.words[:active_index])
                    active_end=active_start+len(cue.words[active_index]["word"])
                    character=0
                    for row in layout["lines"]:
                        indices=[]
                        for index,ch in enumerate(row["text"]):
                            if not ch.isspace():
                                if active_start <= character < active_end:
                                    indices.append(index)
                                character+=1
                        if indices:
                            first,last=indices[0],indices[-1]+1
                            x=row["x"]+draw.textlength(row["text"][:first],font=font)
                            draw.text((x,row["y"]),row["text"][first:last],font=font,fill=c["warm"],anchor="lt",stroke_width=max(1,round(self.width*.002)),stroke_fill=(3,10,18))
        # An unobtrusive progress trace stays inside the same UI-safe zone.
        left,_,right,_=safe_box(self.width,self.height)
        y=round(self.height*.818)
        draw.line((left,y,right,y),fill=(30,51,67),width=max(1,round(self.width*.003)))
        fraction=(self.index+progress)/len(self.story.scenes)
        draw.line((left,y,left+(right-left)*fraction,y),fill=c["accent"],width=max(1,round(self.width*.003)))
        # Keep first/last frames nonempty: short fades blend toward the designed
        # background rather than full black, useful for quality inspection.
        amount=min(_ease(time/.12),_ease((self.duration-time)/.16))
        if amount < 1:
            canvas=Image.blend(self.base,canvas,.65+.35*amount)
        return canvas


def choose_motion_kinds(story: Story) -> list[str]:
    text=(story.title+" "+" ".join(scene.narration for scene in story.scenes)).lower()
    if any(word in text for word in ("кабел","оптоволок","континент")):
        return [CABLE_KINDS[i % len(CABLE_KINDS)] for i in range(len(story.scenes))]
    if any(word in text for word in ("самолёт","самолет","крыло","двигател")):
        return ["airflow" if i%2==0 else "network" for i in range(len(story.scenes))]
    if any(word in text for word in ("шин", "резин", "протектор")):
        return ["tire" if i%2==0 else "tire_tread" for i in range(len(story.scenes))]
    if "звук" in text and any(word in text for word in ("космос", "вакуум", "взрыв")):
        return ["sound" for _ in story.scenes]
    if any(word in text for word in ("батаре","телефон","мороз","электрич")):
        return ["power" if i%2==0 else "network" for i in range(len(story.scenes))]
    if any(word in text for word in ("gps","спутник","космос","земл")):
        return ["orbit" if i%2==0 else "globe_reveal" for i in range(len(story.scenes))]
    return ["network_flow","fiber_detail","orbit","network_flow","globe_reveal","fiber_detail"][:len(story.scenes)] if len(story.scenes)<=6 else ["network" for _ in story.scenes]


def scene_durations(audios: list[Path | None], *, target_seconds: float, fps: int) -> list[float]:
    """Narration duration wins over requested runtime. No speed/rate manipulation."""
    if not audios or not math.isfinite(target_seconds) or not 1 <= target_seconds <= 600:
        raise ValueError("Target duration must be between 1 and 600 seconds")
    if not 1 <= fps <= 60:
        raise ValueError("FPS must be between 1 and 60")
    result=[]
    for audio in audios:
        duration=media_duration(Path(audio))+.08 if audio else target_seconds/len(audios)
        if not math.isfinite(duration) or duration<=0 or duration>600:
            raise ValueError("Unexpected narration duration")
        result.append(math.ceil(duration*fps)/fps)
    return result


def _write_scene(scene: MotionScene, audio: Path | None, segment: Path, fps: int, previews: Path):
    command=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24","-s",f"{scene.width}x{scene.height}","-r",str(fps),"-i","pipe:0"]
    if audio:
        command += ["-i",str(audio)]
        speech_duration=media_duration(Path(audio))
        audio_filter=f"aresample=48000,aformat=channel_layouts=stereo,afade=t=in:st=0:d=0.006,afade=t=out:st={max(0,speech_duration-.008):.6f}:d=0.008,apad"
    else:
        command += ["-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_filter="anull"
    command += ["-t",f"{scene.duration:.6f}","-af",audio_filter,"-map","0:v:0","-map","1:a:0",
                "-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-r",str(fps),
                "-c:a","aac","-b:a","160k","-ar","48000","-ac","2","-movflags","+faststart",str(segment)]
    # stderr goes to a file rather than a pipe to prevent a deadlock during raw
    # frame streaming when an encoder reports repeated failures.
    error_path=segment.with_suffix(".ffmpeg.log")
    frame_count=round(scene.duration*fps)
    with error_path.open("wb") as errors:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors)
        try:
            assert process.stdin is not None
            for frame_index in range(frame_count):
                frame=scene.frame(frame_index/fps)
                if frame_index==min(frame_count-1,max(0,round(frame_count*.52))):
                    frame.save(previews/f"scene_{scene.index+1:02d}.png")
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            returncode=process.wait(timeout=max(120,scene.duration*8))
            if returncode:
                raise RuntimeError(f"FFmpeg scene encoding failed: {error_path.read_text(errors='replace')[-1800:]}")
        except Exception as error:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.kill()
            process.wait()
            if isinstance(error,BrokenPipeError):
                raise RuntimeError(f"FFmpeg rejected frame stream: {error_path.read_text(errors='replace')[-1800:]}") from error
            raise
    error_path.unlink(missing_ok=True)


def _normalize_master(source: Path, output: Path, *, has_speech: bool):
    # Two-pass EBU R128 on the complete narration avoids independently pushing
    # each short clip to a different gain. The measured pass supplies true-peak
    # and integrated loudness to the final pass; silent demos keep a real AAC stream.
    measurement=None
    if has_speech:
        analysis=subprocess.run(["ffmpeg","-hide_banner","-nostats","-i",str(source),"-vn","-af",
                                 "loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json","-f","null","-"],capture_output=True,text=True)
        if analysis.returncode:
            raise RuntimeError(f"Loudness analysis failed: {analysis.stderr[-1200:]}")
        start=analysis.stderr.rfind("{")
        if start>=0:
            try:
                measurement=json.JSONDecoder().raw_decode(analysis.stderr[start:])[0]
            except json.JSONDecodeError:
                measurement=None
        fields=("input_i","input_tp","input_lra","input_thresh","target_offset")
        if not measurement or any(key not in measurement or not math.isfinite(float(measurement[key])) for key in fields):
            raise RuntimeError("Narration loudness is not measurable; inspect the audio before rendering")
        audio_filter=("loudnorm=I=-16:TP=-1.5:LRA=9:"+
                      f"measured_I={measurement['input_i']}:measured_TP={measurement['input_tp']}:"+
                      f"measured_LRA={measurement['input_lra']}:measured_thresh={measurement['input_thresh']}:"+
                      f"offset={measurement['target_offset']}:linear=true:print_format=json")
    else:
        audio_filter="anull"
    _run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-map","0:v:0","-map","0:a:0",
          "-c:v","copy","-af",audio_filter,"-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-movflags","+faststart",str(output)])
    return {"method":"two_pass_ebu_r128" if has_speech else "silent_demo", "target_lufs":-16, "true_peak_ceiling_dbtp":-1.5,
            "analysis":measurement,"no_time_stretch":True,"music":None,"human_voice_audition_required":bool(has_speech)}


def render_motion(story: Story, audios: list[Path | None], dest: Path, *, width: int = 1080,
                  height: int = 1920, fps: int = 30, style: str = "explainer", target_seconds: float = 28,
                  motion_kinds: list[str] | None = None, images: list[Path | None] | None = None,
                  word_timings: dict | None = None) -> tuple[Path,list[float]]:
    """Render portrait H.264/AAC plus SRT/ASS, timeline and geometry manifests."""
    require_ffmpeg()
    if width<90 or height<160 or width%2 or height%2 or width*16 != height*9:
        raise ValueError("Motion output must use even dimensions with an exact 9:16 ratio")
    if len(story.scenes)!=len(audios) or not audios:
        raise ValueError("Each storyboard scene requires one audio entry")
    requested_style=style
    style=style.lower()
    if style in {"whiteboard", "infographic", "cartoon"}:
        style="explainer"
    if style not in PRESETS:
        raise ValueError("Motion style must be cinematic or explainer")
    kinds=motion_kinds or choose_motion_kinds(story)
    if len(kinds)!=len(story.scenes) or (images is not None and len(images)!=len(story.scenes)):
        raise ValueError("Motion kinds and images must match storyboard scenes")
    supported=set(CABLE_KINDS)|{"fiber_detail","network","airflow","power","orbit","tire","tire_tread","sound"}|set(ALIASES)
    if any(kind not in supported for kind in kinds):
        raise ValueError("Unknown motion kind in storyboard")
    dest=Path(dest)
    dest.mkdir(parents=True,exist_ok=True)
    previews=dest/"previews"
    previews.mkdir(exist_ok=True)
    prepared = []
    audio_edits = []
    for index, audio in enumerate(audios):
        if audio is None:
            prepared.append(None)
            continue
        rendered_audio, edit = prepare_narration(Path(audio), dest, index)
        prepared.append(rendered_audio)
        audio_edits.append({"scene": index, **edit})
    durations=scene_durations(prepared,target_seconds=target_seconds,fps=fps)
    caption_manifest=write_captions(story,durations,dest,width=width,height=height,word_timings=word_timings)
    cues=build_caption_cues(story,durations,word_timings)
    segment_dir=dest/"segments"
    segment_dir.mkdir(exist_ok=True)
    scene_rows=[]
    all_geometry=[]
    segments=[]
    offset=0.
    for index,(duration,audio,kind) in enumerate(zip(durations,prepared,kinds)):
        scene_cues=[cue for cue in cues if cue.scene==index]
        scene=MotionScene(story,index,duration,width,height,style,kind,scene_cues,images[index] if images else None)
        scene.scene_offset=offset
        segment=segment_dir/f"motion_{index:02d}.mp4"
        _write_scene(scene,Path(audio) if audio else None,segment,fps,previews)
        all_geometry += [{"scene":index,**row} for row in scene.text_geometries]
        scene_rows.append({"scene":index,"start":round(offset,6),"end":round(offset+duration,6),"duration":duration,
                           "motion_kind":scene.kind,"frames":round(duration*fps),"narration":display_text(story.scenes[index].narration),
                           "audio":str(Path(audios[index]).name) if audios[index] else None,
                           "prepared_audio":str(Path(audio).relative_to(dest)) if audio else None,
                           "visual_type":"still_image_with_local_motion_and_procedural_overlay" if scene.image else "original_procedural_animation",
                           "visual_beats_seconds":[round(duration*q,3) for q in (.0,.28,.5,.75)],
                           "source_image":str(Path(images[index]).name) if images and images[index] else None})
        offset+=duration
        segments.append(segment)
    # Quoting is FFmpeg concat-file escaping (not shell escaping); subprocess is
    # never invoked through a shell, including for paths containing whitespace.
    playlist=dest/"motion_concat.txt"
    def quoted(path: Path) -> str:
        return str(path.resolve()).replace("'", "'\\''")
    playlist.write_text("".join(f"file '{quoted(segment)}'\n" for segment in segments),encoding="utf-8")
    joined=dest/"unnormalized.mp4"
    _run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(playlist),
          "-c","copy","-movflags","+faststart",str(joined)])
    output=dest/"short.mp4"
    audio_manifest=_normalize_master(joined,output,has_speech=any(audio is not None for audio in audios))
    audio_manifest["silence_editing"] = {
        "applied": bool(audio_edits),
        "scenes": audio_edits,
        "time_stretch": False,
        "phrase_alignment": "estimated per phrase unless verified timings are supplied",
    }
    (dest/"audio_master.json").write_text(json.dumps(audio_manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    joined.unlink(missing_ok=True)
    geometry={"width":width,"height":height,"safe_box":list(safe_box(width,height)),
              "all_text_within_safe_zone":all(row["inside_safe_zone"] for row in all_geometry),
              "evidence":"Glyph bounds measured with Pillow and the same Cyrillic font and positions used to burn captions.",
              "main_illustration_box":[round(width*.1),round(height*.28),round(width*.87),round(height*.65)],
              "text":all_geometry}
    (dest/"geometry.json").write_text(json.dumps(geometry,ensure_ascii=False,indent=2),encoding="utf-8")
    timeline={"version":1,"renderer":"original_pillow_procedural_motion","style":style,"requested_style":requested_style,"fps":fps,"width":width,"height":height,
              "target_seconds":target_seconds,"actual_seconds":sum(durations),"duration_policy":"audio_first_no_speedup",
              "caption_timing":caption_manifest["timing_mode"],"scenes":scene_rows}
    (dest/"timeline.json").write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding="utf-8")
    return output,durations
