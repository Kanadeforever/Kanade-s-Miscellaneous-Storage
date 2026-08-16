from PIL import Image, ImageDraw, ImageFilter
import struct, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
RES = ROOT / 'src' / 'resources'
RES.mkdir(parents=True, exist_ok=True)

# 以 1024px 绘制再缩小，保证 16/24px 小图标仍有平滑边缘。
S = 1024
img = Image.new('RGBA', (S,S), (0,0,0,0))

# 主体底板：Windows 11 风格圆角深色玻璃块。
bg = Image.new('RGBA',(S,S),(0,0,0,0))
d = ImageDraw.Draw(bg)
d.rounded_rectangle((86,86,938,938), radius=210, fill=(18,35,58,246))
# 轻微纵向渐变高光。
grad = Image.new('RGBA',(S,S),(0,0,0,0))
gp = grad.load()
for y in range(86,939):
    a = int(70 * (1 - (y-86)/853))
    for x in range(86,939):
        gp[x,y] = (54,156,255,a)
grad.putalpha(grad.getchannel('A'))
bg = Image.alpha_composite(bg, grad)
img = Image.alpha_composite(img,bg)

# 文件夹：左上凸起 + 柔和蓝色主体。
folder = Image.new('RGBA',(S,S),(0,0,0,0))
fd = ImageDraw.Draw(folder)
fd.rounded_rectangle((210,324,814,746), radius=92, fill=(46,151,246,255))
fd.rounded_rectangle((210,265,490,410), radius=72, fill=(74,178,255,255))
fd.rounded_rectangle((240,350,785,715), radius=70, fill=(70,178,255,255))
# 文件夹内部模拟三个 Explorer 标签页。
fd.rounded_rectangle((315,415,455,478), radius=28, fill=(241,249,255,245))
fd.rounded_rectangle((465,415,605,478), radius=28, fill=(202,234,255,238))
fd.rounded_rectangle((615,415,705,478), radius=28, fill=(202,234,255,205))
fd.rounded_rectangle((315,500,705,650), radius=40, fill=(24,91,152,110))
# 柔和阴影
shadow = folder.getchannel('A').filter(ImageFilter.GaussianBlur(24))
sh = Image.new('RGBA',(S,S),(4,18,35,0)); sh.putalpha(shadow.point(lambda p: int(p*0.33)))
sh2 = Image.new('RGBA',(S,S),(0,0,0,0)); sh2.alpha_composite(sh,(10,18))
img = Image.alpha_composite(img,sh2)
img = Image.alpha_composite(img,folder)

# 恢复箭头：围绕右下角，表达 restart + restore，而不是普通刷新。
a = Image.new('RGBA',(S,S),(0,0,0,0))
ad = ImageDraw.Draw(a)
# 用多段弧线产生粗圆弧
bbox=(470,455,882,867)
for w in range(0,46):
    ad.arc((bbox[0]+w/2,bbox[1]+w/2,bbox[2]-w/2,bbox[3]-w/2), start=42, end=315, fill=(247,253,255,255), width=3)
# 箭头头部
ad.polygon([(830,445),(930,493),(852,558)], fill=(247,253,255,255))
# 中心小“恢复点”增强辨识度
ad.ellipse((639,625,708,694), fill=(142,242,216,255))
img = Image.alpha_composite(img,a)

# 边缘光
edge = Image.new('RGBA',(S,S),(0,0,0,0)); ed=ImageDraw.Draw(edge)
ed.rounded_rectangle((87,87,937,937),radius=210,outline=(126,207,255,120),width=7)
img = Image.alpha_composite(img,edge)

png_path = RES/'ExplorerRescue.png'
img.save(png_path)

ico_path = RES/'ExplorerRescue.ico'
img.save(ico_path, format='ICO', sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)])

# ---------- 直接生成 Win32 .res ----------
def align4(b: bytearray):
    while len(b) % 4:
        b += b'\0'

def id_field(i: int) -> bytes:
    return struct.pack('<HH', 0xFFFF, i)

def record(rtype: int, name: int, data: bytes, memflags=0x1030, lang=0):
    hdr = bytearray(struct.pack('<II', len(data), 0))
    hdr += id_field(rtype)
    align4(hdr)
    hdr += id_field(name)
    align4(hdr)
    hdr += struct.pack('<IHHII', 0, memflags, lang, 0, 0)
    struct.pack_into('<I', hdr, 4, len(hdr))
    out = hdr + data
    align4(out)
    return bytes(out)

def null_record():
    return record(0,0,b'',memflags=0,lang=0)

raw = ico_path.read_bytes()
reserved, typ, count = struct.unpack_from('<HHH', raw, 0)
assert reserved == 0 and typ == 1
entries=[]
for i in range(count):
    off=6+i*16
    width,height,color,resv,planes,bpp,size,dataoff=struct.unpack_from('<BBBBHHII',raw,off)
    blob=raw[dataoff:dataoff+size]
    entries.append((width,height,color,resv,planes,bpp,size,blob))

res = bytearray(null_record())
for idx,e in enumerate(entries,1):
    res += record(3,idx,e[7]) # RT_ICON

grp = bytearray(struct.pack('<HHH',0,1,len(entries)))
for idx,e in enumerate(entries,1):
    grp += struct.pack('<BBBBHHIH',e[0],e[1],e[2],e[3],e[4],e[5],e[6],idx)
res += record(14,101,bytes(grp)) # RT_GROUP_ICON

manifest = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">\r\n  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">\r\n    <security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security>\r\n  </trustInfo>\r\n  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">\r\n    <application><supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/></application>\r\n  </compatibility>\r\n  <application xmlns="urn:schemas-microsoft-com:asm.v3">\r\n    <windowsSettings>\r\n      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>\r\n      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>\r\n    </windowsSettings>\r\n  </application>\r\n</assembly>\r\n'''
res += record(24,1,manifest,memflags=0x1030,lang=0) # RT_MANIFEST

res_path = RES/'ExplorerRescue.res'
res_path.write_bytes(res)
print(png_path)
print(ico_path)
print(res_path, len(res))
