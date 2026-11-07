"""Identify changed registered footer ads; never infer a new click position."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Appearance:
    title: bytes
    body: bytes

    def stable_with(self,other):
        return all(sum(abs(x-y) for x,y in zip(a,b))/len(a)<=5
                   for a,b in ((self.title,other.title),(self.body,other.body)))

    def different_from(self,other):
        # Both title and body must materially change. A small animation,
        # cursor, footer checkbox, or a flashing price is not a new advert.
        return all(sum(abs(x-y) for x,y in zip(a,b))/len(a)>=12 and
                   sum(abs(x-y)>=20 for x,y in zip(a,b))/len(a)>=.25
                   for a,b in ((self.title,other.title),(self.body,other.body)))


def footer_ad(record):
    roi=record.get('roi');point=record.get('click_point')
    return (record.get('click_mode')=='fixed' and record.get('size')==[720,1280]
            and isinstance(roi,(list,tuple)) and len(roi)==4 and roi[1]>=1150
            and isinstance(point,(list,tuple)) and len(point)==2
            and (50<=point[0]<=350 or 620<=point[0]<=675) and 1200<=point[1]<=1255)


def read_appearances(png,records,found):
    keys=[key for key,present in found.items() if present and footer_ad(records[key])]
    if not keys:return {}
    import cv2
    import numpy as np
    from vision import decode_screen
    screen=decode_screen(png)
    if screen is None or screen.shape!=(1280,720):return {}
    result={}
    for key in keys:
        point=records[key]['click_point']
        x,y=point if point[0]>=620 else (646,1225)
        ring=[screen[y+dy-1:y+dy+2,x+dx-1:x+dx+2] for dx,dy in ((18,0),(-18,0),(0,18),(0,-18))]
        if any(float(np.median(patch))<160 for patch in ring):continue
        # Positively verify the dark X inside its light circle, at the already
        # registered close point; neither prices nor purchase buttons qualify.
        if any(sum(int(screen[y+sign*d-4:y+sign*d+5,x+d-4:x+d+5].min())<110
                   for d in (-12,-8,-4,0,4,8,12))<5 for sign in (-1,1)):continue
        title=cv2.resize(screen[55:425,65:655],(24,16),interpolation=cv2.INTER_AREA).tobytes()
        body=cv2.resize(screen[470:1130,65:655],(24,26),interpolation=cv2.INTER_AREA).tobytes()
        result[key]=Appearance(title,body)
    return result
