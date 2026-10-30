# gen_mains_wiring.py - draws docs/wiring-mains.png, the 230 V side (run on a
# computer, needs Pillow):
#   python3 tools/gen_mains_wiring.py
import os

from PIL import Image, ImageDraw, ImageFont
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","docs","wiring-mains.png")
S=2
I="/System/Library/Fonts/SFNS.ttf"
_fonts={}
def f(sz,w="Regular"):
    if (sz,w) not in _fonts:
        ft=ImageFont.truetype(I,int(sz*S)); ft.set_variation_by_name(w.replace("SemiBold","Semibold")); _fonts[(sz,w)]=ft
    return _fonts[(sz,w)]
BG="#011515"; SURF="#041C1B"; SURFL="#062523"; TXT="#F2F5F4"; MUT="#8A9B9B"; BORD="#123333"
GREEN="#00E95A"; CYAN="#20DCE8"; BLUE="#78BFFF"; YEL="#FFD21C"; PUR="#B18CFF"; RED="#FF6070"; ORA="#FF9F43"
LC=RED; NC=BLUE
W=1600
notes=["230 V AC: work only with the box unplugged. Use insulated screw terminals and wire rated for the load.",
"The relays break the phase (L). A Schuko plug fits both ways, so L and N can swap: unplug before touching the lamp or the fan.",
"Relay IN1 (lamp) = GPIO 23, IN2 (fan) = GPIO 22, dimmer PWM = GPIO 21. Low-voltage side: see the controller wiring.",
"Protective earth (PE) goes straight from the plug to both sockets and is never switched (not drawn)."]
H0=780; H=H0+len(notes)*34+50
im=Image.new("RGB",(W*S,H*S),BG); d=ImageDraw.Draw(im)
def P(*a): return tuple(int(v*S) for v in a)
def rr(box,r,fill,outline=None,width=1): d.rounded_rectangle(P(*box),int(r*S),fill=fill,outline=outline,width=int(width*S))
def T(xy,t,font,fill,anchor="la"): d.text(P(*xy),t,font=font,fill=fill,anchor=anchor)
def path(pts,c,w=4):
    d.line([P(*p) for p in pts],fill=c,width=int(w*S),joint="curve")
def circ(x,y,r,fill,outline=None,w=0): d.ellipse(P(x-r,y-r,x+r,y+r),fill=fill,outline=outline,width=int(w*S))
def pad(x,y,c): circ(x,y,9,BG,c,3)
def dot(x,y,c): circ(x,y,7,c)
def dashed(a,b,c,w=2,dl=10):
    (x0,y0),(x1,y1)=a,b; n=int(max(abs(x1-x0),abs(y1-y0))/dl)
    for i in range(0,n,2):
        t0,t1=i/n,min((i+1)/n,1); line=[(x0+(x1-x0)*t0,y0+(y1-y0)*t0),(x0+(x1-x0)*t1,y0+(y1-y0)*t1)]
        d.line([P(*p) for p in line],fill=c,width=int(w*S))
T((48,40),"GrowBox mains wiring",f(40,"Bold"),TXT)
T((48,96),"230 V · 2× relay module · fan dimmer · lamp and fan sockets",f(22,"Medium"),CYAN)
T((48,130),"How the lamp and the fan get their power. L = phase, N = neutral.",f(17),MUT)
rr((60,370,250,530),22,SURF,BORD,2)
T((80,386),"Mains plug",f(21,"Bold"),TXT); T((80,414),"230 V AC",f(15),MUT)
pl,pn=(250,450),(250,500)
for (x,y),lab,c in ((pl,"L",LC),(pn,"N",NC)): T((x-18,y),lab,f(18,"SemiBold"),c,"rm")
rx0,rx1,ry0,ry1=560,860,220,580
rr((rx0,ry0,rx1,ry1),24,SURFL,GREEN,2)
T(((rx0+rx1)/2,ry0+40),"2× relay",f(26,"Bold"),TXT,"mm"); T(((rx0+rx1)/2,ry0+70),"5 V module",f(15),MUT,"mm")
for y,ch,lab in ((310,"CH1","lamp"),(490,"CH2","fan")):
    rr((rx0+70,y-34,rx1-70,y+34),14,SURF,BORD,2)
    T(((rx0+rx1)/2,y-8),ch,f(18,"Bold"),TXT,"mm"); T(((rx0+rx1)/2,y+16),lab,f(14),MUT,"mm")
    T((rx0+20,y-24),"COM",f(13,"SemiBold"),MUT); T((rx1-20,y-24),"NO",f(13,"SemiBold"),MUT,"ra")
dx0,dx1,dy0,dy1=1000,1220,430,660
rr((dx0,dy0,dx1,dy1),24,SURFL,PUR,2)
T(((dx0+dx1)/2,dy0+30),"Fan dimmer",f(22,"Bold"),TXT,"mm"); T(((dx0+dx1)/2,dy0+56),"PWM controlled",f(14),MUT,"mm")
T((dx0+20,490+24),"L in",f(13,"SemiBold"),MUT); T((dx1-20,490+24),"L out",f(13,"SemiBold"),MUT,"ra")
T((dx1-20,600-24),"N out",f(13,"SemiBold"),MUT,"ra"); T((1080,dy1-22),"N in",f(13,"SemiBold"),MUT,"mm")
def sock(y0,y1,title,sub,col):
    rr((1340,y0,1480,y1),22,SURF,BORD,2)
    T((1410,y0+24),title,f(20,"Bold"),col,"mm"); T((1410,y0+48),sub,f(14),MUT,"mm")
sock(240,380,"Lamp","socket",YEL)
sock(440,660,"Fan","socket",CYAN)
for x,lab in ((660,"IN1 · GPIO 23"),(760,"IN2 · GPIO 22")):
    dashed((x,ry0-60),(x,ry0),MUT); pad(x,ry0,MUT)
T((710,ry0-74),"from ESP32",f(14,"SemiBold"),MUT,"mm")
T((648,ry0-30),"IN1",f(13,"SemiBold"),MUT,"rm"); T((772,ry0-30),"IN2",f(13,"SemiBold"),MUT,"lm")
dashed((1110,dy0-60),(1110,dy0),MUT); pad(1110,dy0,MUT); T((1124,dy0-40),"PWM · GPIO 21",f(13,"SemiBold"),MUT,"lm")
path([pl,(420,450)],LC); path([(420,310),(420,490)],LC)
path([(420,310),(rx0,310)],LC); path([(420,490),(rx0,490)],LC); dot(420,450,LC)
path([(rx1,310),(1340,310)],LC)
path([(rx1,490),(dx0,490)],LC)
path([(dx1,490),(1340,490)],LC)
path([pn,(330,500),(330,730),(1540,730),(1540,340),(1480,340)],NC)
path([(1080,730),(1080,dy1)],NC); dot(1080,730,NC)
path([(dx1,600),(1340,600)],NC)
for p,c in ((pl,LC),(pn,NC),((rx0,310),LC),((rx0,490),LC),((rx1,310),LC),((rx1,490),LC),((dx0,490),LC),((dx1,490),LC),((dx1,600),NC),((1080,dy1),NC),((1340,310),LC),((1340,490),LC),((1340,600),NC),((1480,340),NC)):
    pad(*p,c)
for (x,y),lab,c,a in (((1330,310),"L",LC,"rm"),((1330,490),"L",LC,"rm"),((1330,600),"N",NC,"rm"),((1490,340),"N",NC,"lm")):
    T((x-10 if a=="rm" else x+10,y-18),lab,f(15,"Bold"),c,"rm" if a=="rm" else "lm")
T((700,750),"N (neutral)",f(15,"SemiBold"),NC,"mm"); T((440,380),"L (phase)",f(15,"SemiBold"),LC,"lm")
y=H0+20
for n in notes:
    circ(56,y,4,GREEN); T((72,y),n,f(17),TXT,"lm"); y+=34
im=im.resize((W,H),Image.LANCZOS); im.save(OUT)
print(OUT)
