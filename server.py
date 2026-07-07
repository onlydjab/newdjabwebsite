#!/usr/bin/env python3
"""DJ AB — Website Dashboard Server"""

import base64, json, os, re, html as html_lib, smtplib, ssl, sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PORT      = 3456
BASE      = os.path.dirname(os.path.abspath(__file__))
PUBLIC    = os.path.join(BASE, 'docs')
SITE      = os.path.join(PUBLIC, 'index.html')
CFG       = os.path.join(BASE, 'config.json')
SUBS      = os.path.join(BASE, 'submissions.json')
DASH      = os.path.join(BASE, 'dashboard.html')
AUDIO_DIR = os.path.join(PUBLIC, 'audio')

AUDIO_MIME = {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
}

IMAGES_DIR = os.path.join(PUBLIC, 'images')

IMAGE_MIME = {
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.gif':  'image/gif',
    '.webp': 'image/webp',
    '.avif': 'image/avif',
}

EPK_DIR = os.path.join(PUBLIC, 'epk')

EPK_MIME = {
    '.pdf':  'application/pdf',
    '.zip':  'application/zip',
    '.doc':  'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}

ENV_FILE = os.path.join(BASE, '.env')

# ── .env loading (no external dependency) ─────────────────────────────────────

def load_dotenv(path):
    if not os.path.isfile(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

def write_env_var(key, value):
    """Upsert a single key=value line in the local, gitignored .env file."""
    lines = []
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f'{key}='):
            lines[i] = f'{key}={value}\n'
            found = True
            break
    if not found:
        lines.append(f'{key}={value}\n')
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    os.environ[key] = value

load_dotenv(ENV_FILE)

def get_email_pass():
    return os.environ.get('EMAIL_APP_PASSWORD', '')

DASHBOARD_USER = os.environ.get('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASS = os.environ.get('DASHBOARD_PASSWORD', '')

# ── dashboard authentication (HTTP Basic Auth) ────────────────────────────────

def check_auth(handler):
    """Dashboard access is refused until DASHBOARD_PASSWORD is set in .env —
    no built-in default password, so nothing is left accidentally open."""
    if not DASHBOARD_PASS:
        return False
    auth = handler.headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode('utf-8')
        user, _, pw = decoded.partition(':')
    except Exception:
        return False
    return user == DASHBOARD_USER and pw == DASHBOARD_PASS

def require_auth(handler):
    body = b'Authentication required.'
    handler.send_response(401)
    handler.send_header('WWW-Authenticate', 'Basic realm="DJ AB Dashboard"')
    handler.send_header('Content-Type', 'text/plain')
    handler.send_header('Content-Length', len(body))
    handler.end_headers()
    handler.wfile.write(body)

# Paths that expose PII, credentials, or let content/config be changed.
# Everything the *public* site itself needs at runtime (images, audio, the
# read-only /api/images|socials|epk|colorscheme endpoints) stays open so the
# local preview keeps working like the real deployed site would.
DASH_GET_PROTECTED = {'/dashboard', '/api/content', '/api/submissions', '/api/email-config'}

# ── file helpers ──────────────────────────────────────────────────────────────

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

def read_json(path, default):
    try:
        return json.loads(read_file(path))
    except Exception:
        return default

def write_json(path, data):
    write_file(path, json.dumps(data, indent=2, ensure_ascii=False))

# ── HTML helpers ──────────────────────────────────────────────────────────────

def h_esc(text):
    return html_lib.escape(str(text or ''), quote=True)

def h_raw(html):
    """Strip inner tags and unescape entities → plain text."""
    return html_lib.unescape(re.sub(r'<[^>]+>', '', html)).strip()

def get_class_text(raw, cls, tag='div', n=0):
    pat = rf'<{tag}[^>]*\bclass="(?:[^"]*\s)?{re.escape(cls)}(?:\s[^"]*)?"[^>]*>(.*?)</{tag}>'
    hits = re.findall(pat, raw, re.DOTALL)
    return h_raw(hits[n]) if n < len(hits) else ''

def set_class_text(raw, cls, tag, text, n=0):
    pat = rf'(<{tag}[^>]*\bclass="(?:[^"]*\s)?{re.escape(cls)}(?:\s[^"]*)?"[^>]*>)(.*?)(</{tag}>)'
    count = [0]
    def rep(m):
        if count[0] == n:
            count[0] += 1
            return m.group(1) + h_esc(text) + m.group(3)
        count[0] += 1
        return m.group(0)
    return re.sub(pat, rep, raw, flags=re.DOTALL)

# ── content extraction ────────────────────────────────────────────────────────

def extract_content(raw):
    # Hero
    hero_tag = get_class_text(raw, 'hero-tag', 'span')
    hero_sub = get_class_text(raw, 'hero-sub', 'p')

    # About bio paragraphs (inside .about-text)
    about_m = re.search(r'<div class="about-text">(.*?)</div>', raw, re.DOTALL)
    bio = [h_raw(p) for p in re.findall(r'<p>(.*?)</p>', about_m.group(1), re.DOTALL)] if about_m else []

    # Stats
    stat_pat = re.compile(
        r'<div class="stat-box">\s*<div class="stat-num">(.*?)</div>\s*<div class="stat-label">(.*?)</div>\s*</div>',
        re.DOTALL
    )
    stats = [{'num': h_raw(m[0]), 'label': h_raw(m[1])} for m in stat_pat.findall(raw)]

    # Service cards
    svc_pat = re.compile(
        r'<div class="service-card reveal">\s*(?:<div class="service-card-img">.*?</div>\s*)?<div class="service-icon">(.*?)</div>\s*<h3>(.*?)</h3>\s*<p>(.*?)</p>\s*</div>',
        re.DOTALL
    )
    services = [{'icon': h_raw(m[0]), 'title': h_raw(m[1]), 'description': h_raw(m[2])}
                for m in svc_pat.findall(raw)]

    # Mix tracks (title, genre, duration, audioFile from data-audio attribute)
    titles    = [h_raw(t) for t in re.findall(r'<div class="track-title">(.*?)</div>', raw, re.DOTALL)]
    genres_mx = [h_raw(g) for g in re.findall(r'<div class="track-genre">(.*?)</div>', raw, re.DOTALL)]
    durs      = [h_raw(d) for d in re.findall(r'<div class="track-duration">(.*?)</div>', raw, re.DOTALL)]
    audio_files = re.findall(r'<div class="mix-track"[^>]*\bdata-audio="([^"]*)"', raw)
    mixes = [{'title':    titles[i],
              'genre':    genres_mx[i] if i < len(genres_mx) else '',
              'duration': durs[i]      if i < len(durs)      else '',
              'audioFile': audio_files[i] if i < len(audio_files) else ''}
             for i in range(len(titles))]

    # Genre tags
    genres = [h_raw(g) for g in re.findall(r'<span class="genre-tag">(.*?)</span>', raw)]

    # Booked dates from JS
    bd_m = re.search(r"const bookedDates = new Set\(\[([\s\S]*?)\]\)", raw)
    booked = sorted(set(re.findall(r"'(\d{4}-\d{2}-\d{2})'", bd_m.group(1)))) if bd_m else []

    return dict(heroTag=hero_tag, heroSub=hero_sub, bio=bio, stats=stats,
                services=services, mixes=mixes, genres=genres, bookedDates=booked)

# ── content updates ───────────────────────────────────────────────────────────

def update_about(raw, data):
    if 'heroTag' in data:
        raw = set_class_text(raw, 'hero-tag', 'span', data['heroTag'])
    if 'heroSub' in data:
        raw = set_class_text(raw, 'hero-sub', 'p', data['heroSub'])
    if 'bio' in data:
        paras = '\n'.join(f'          <p>{h_esc(p)}</p>' for p in data['bio'])
        raw = re.sub(
            r'(<div class="about-text">).*?(</div>)',
            lambda m: m.group(1) + '\n' + paras + '\n        ' + m.group(2),
            raw, count=1, flags=re.DOTALL
        )
    if 'stats' in data:
        for i, s in enumerate(data['stats']):
            raw = set_class_text(raw, 'stat-num',   'div', s['num'],   n=i)
            raw = set_class_text(raw, 'stat-label', 'div', s['label'], n=i)
    return raw

def update_services(raw, services):
    def make_card(s, img_block):
        img_part = f'{img_block}\n        ' if img_block else ''
        return (
            f'<div class="service-card reveal">\n'
            f'        {img_part}<div class="service-icon">{h_esc(s["icon"])}</div>\n'
            f'        <h3>{h_esc(s["title"])}</h3>\n'
            f'        <p>{h_esc(s["description"])}</p>\n'
            f'      </div>'
        )
    pat = re.compile(
        r'<div class="service-card reveal">\s*((?:<div class="service-card-img">.*?</div>)?)\s*<div class="service-icon">.*?</div>\s*<h3>.*?</h3>\s*<p>.*?</p>\s*</div>',
        re.DOTALL
    )
    cards = list(pat.finditer(raw))
    offset = 0
    for i, m in enumerate(cards):
        if i >= len(services):
            break
        replacement = make_card(services[i], m.group(1))
        raw = raw[:m.start() + offset] + replacement + raw[m.end() + offset:]
        offset += len(replacement) - (m.end() - m.start())
    return raw

def update_mixes(raw, mixes):
    for i, mix in enumerate(mixes):
        raw = set_class_text(raw, 'track-title',    'div', mix['title'],    n=i)
        raw = set_class_text(raw, 'track-genre',    'div', mix['genre'],    n=i)
        raw = set_class_text(raw, 'track-duration', 'div', mix['duration'], n=i)
    return raw

def update_mix_audio(raw, idx, filename):
    """Update the data-audio attribute on the idx-th .mix-track."""
    pat = re.compile(r'<div class="mix-track"([^>]*)\bdata-audio="[^"]*"')
    matches = list(pat.finditer(raw))
    if idx >= len(matches):
        return raw
    m = matches[idx]
    safe = filename.replace('"', '').replace('/', '').replace('\\', '')
    replacement = f'<div class="mix-track"{m.group(1)}data-audio="{safe}"'
    return raw[:m.start()] + replacement + raw[m.end():]

def update_genres(raw, genres):
    new_tags = '\n        '.join(f'<span class="genre-tag">{h_esc(g)}</span>' for g in genres)
    return re.sub(
        r'(<div class="genre-tags">)\s*(.*?)(\s*</div>)',
        lambda m: m.group(1) + '\n        ' + new_tags + '\n      ' + m.group(3),
        raw, count=1, flags=re.DOTALL
    )

def update_booked_dates(raw, dates):
    sorted_dates = sorted(set(dates))
    formatted = ',\n'.join(f"    '{d}'" for d in sorted_dates)
    return re.sub(
        r"const bookedDates = new Set\(\[([\s\S]*?)\]\)",
        f"const bookedDates = new Set([\n{formatted},\n  ])",
        raw
    )

# ── image slot assignment (about / service / header / banner) ────────────────

def set_image_slot(cfg, target, filename):
    """Assign filename (or '' to clear) to a named slot. Returns True if target was valid."""
    if target == 'about':
        cfg['aboutImage'] = filename
    elif target == 'header':
        cfg['headerImage'] = filename
    elif target == 'banner':
        cfg['bannerImage'] = filename
    elif target.startswith('service/'):
        idx = int(target.split('/')[-1])
        imgs = cfg.get('serviceImages', ['', '', '', ''])
        while len(imgs) <= idx:
            imgs.append('')
        imgs[idx] = filename
        cfg['serviceImages'] = imgs
    else:
        return False
    return True

# ── color scheme ──────────────────────────────────────────────────────────────

def hex_to_rgb(hex_color):
    h = hex_color.lstrip('#')
    return f'{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}'

def update_color_scheme(raw, old_scheme, new_scheme):
    for key in ('accent', 'accent2'):
        old_hex = old_scheme.get(key, '')
        new_hex = new_scheme.get(key, '')
        if not old_hex or not new_hex or old_hex.lower() == new_hex.lower():
            continue
        raw = raw.replace(old_hex, new_hex)
        raw = raw.replace(hex_to_rgb(old_hex), hex_to_rgb(new_hex))
    return raw

# ── multipart file upload parsing ─────────────────────────────────────────────

def parse_multipart(content_type, body):
    """Parse multipart/form-data. Returns (safe_filename, raw_filename, bytes) or (None, None, None)."""
    m = re.search(r'boundary=([^\s;]+)', content_type)
    if not m:
        return None, None, None
    boundary = m.group(1).strip('"').strip()
    delimiter = b'--' + boundary.encode('ascii')

    parts = body.split(delimiter)
    for part in parts[1:]:
        if part[:2] == b'--':
            break
        sep = b'\r\n\r\n' if b'\r\n\r\n' in part else b'\n\n'
        if sep not in part:
            continue
        header_bytes, content = part.split(sep, 1)
        if content.endswith(b'\r\n'):
            content = content[:-2]
        headers_text = header_bytes.decode('utf-8', errors='replace')
        fn_m = re.search(r'filename="([^"]*)"', headers_text)
        if fn_m and fn_m.group(1):
            raw_filename = os.path.basename(fn_m.group(1))
            filename = re.sub(r'[^\w\-_.]', '_', raw_filename)
            return filename, raw_filename, content
    return None, None, None

# ── email ─────────────────────────────────────────────────────────────────────

def send_email(cfg, to, subject, body_html, reply_to=None):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"DJ AB Website <{cfg['emailUser']}>"
    msg['To']      = to
    if reply_to:
        msg['Reply-To'] = reply_to
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as s:
        s.login(cfg['emailUser'], get_email_pass())
        s.sendmail(cfg['emailUser'], [to], msg.as_string())

def inquiry_html(data):
    rows = [
        ('Name',       data.get('name', '—')),
        ('Email',      data.get('email', '—')),
        ('Phone',      data.get('phone') or '—'),
        ('Event Date', data.get('event-date') or '—'),
        ('Event Type', data.get('event-type') or '—'),
        ('Message',    data.get('message') or '—'),
    ]
    row_html = ''.join(
        f'<tr style="background:{"#f5f5f5" if i%2 else "#fff"}">'
        f'<td style="padding:10px 14px;font-weight:700;width:130px;color:#333">{h_esc(k)}</td>'
        f'<td style="padding:10px 14px;color:#444">{h_esc(v)}</td></tr>'
        for i, (k, v) in enumerate(rows)
    )
    return (
        '<div style="font-family:sans-serif;max-width:600px">'
        '<h2 style="color:#00d4ff;margin:0 0 20px">New Booking Inquiry</h2>'
        f'<table style="border-collapse:collapse;width:100%">{row_html}</table>'
        '<p style="margin-top:20px;color:#888;font-size:12px">Sent from the DJ AB website contact form.</p>'
        '</div>'
    )

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f'  {self.address_string()} {fmt % args}')

    # ── send helpers ──────────────────────────────────────────────────────────

    def send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        try:
            body = read_file(path).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def send_static_file(self, path, mime_type):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            print(f'  [file error] {e}')
            self.send_error(500)

    def send_download_file(self, path, mime_type, download_name):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            safe_name = re.sub(r'[\r\n"]', '', download_name)
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', len(data))
            self.send_header('Content-Disposition', f'attachment; filename="{safe_name}"')
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            print(f'  [file error] {e}')
            self.send_error(500)

    def send_audio_file(self, path, mime_type):
        try:
            size = os.path.getsize(path)
            range_header = self.headers.get('Range', '')
            if range_header:
                m = re.match(r'bytes=(\d*)-(\d*)', range_header)
                if m:
                    start = int(m.group(1)) if m.group(1) else 0
                    end   = int(m.group(2)) if m.group(2) else size - 1
                    end   = min(end, size - 1)
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header('Content-Type', mime_type)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.send_header('Content-Length', length)
                    self.send_header('Accept-Ranges', 'bytes')
                    self.end_headers()
                    with open(path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', size)
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:
            print(f'  [audio error] {e}')

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def read_body_raw(self):
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return b''
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        path = urlparse(self.path).path

        if path in DASH_GET_PROTECTED and not check_auth(self):
            require_auth(self)
            return

        if path == '/':
            self.send_html(SITE)

        elif path == '/dashboard':
            self.send_html(DASH)

        elif path == '/api/content':
            try:
                raw = read_file(SITE)
                self.send_json(extract_content(raw))
            except Exception as e:
                self.send_json({'error': str(e)}, 500)

        elif path == '/api/email-config':
            cfg = read_json(CFG, {})
            self.send_json({
                'emailUser':  cfg.get('emailUser', ''),
                'emailTo':    cfg.get('emailTo', 'inquiries.djab@gmail.com'),
                'configured': bool(cfg.get('emailUser') and get_email_pass())
            })

        elif path == '/api/submissions':
            self.send_json(read_json(SUBS, []))

        elif path == '/api/socials':
            cfg = read_json(CFG, {})
            self.send_json(cfg.get('socials', {
                'tiktok_url': '', 'tiktok_handle': '@djab',
                'instagram_url': '', 'instagram_handle': '@djab',
                'youtube_url': '', 'youtube_handle': 'DJ AB',
            }))

        elif path.startswith('/audio/'):
            filename = os.path.basename(path[7:])
            if not filename:
                self.send_error(400)
                return
            audio_path = os.path.join(AUDIO_DIR, filename)
            ext = os.path.splitext(filename)[1].lower()
            mime = AUDIO_MIME.get(ext, 'application/octet-stream')
            self.send_audio_file(audio_path, mime)

        elif path.startswith('/images/'):
            filename = os.path.basename(path[8:])
            if not filename:
                self.send_error(400)
                return
            img_path = os.path.join(IMAGES_DIR, filename)
            ext = os.path.splitext(filename)[1].lower()
            mime = IMAGE_MIME.get(ext, 'application/octet-stream')
            self.send_static_file(img_path, mime)

        elif path == '/api/images':
            cfg = read_json(CFG, {})
            self.send_json({
                'aboutImage':    cfg.get('aboutImage', ''),
                'serviceImages': cfg.get('serviceImages', ['', '', '', '']),
                'headerImage':   cfg.get('headerImage', ''),
                'bannerImage':   cfg.get('bannerImage', ''),
            })

        elif path == '/api/gallery':
            try:
                files = sorted(
                    f for f in os.listdir(IMAGES_DIR)
                    if os.path.splitext(f)[1].lower() in IMAGE_MIME
                )
            except FileNotFoundError:
                files = []
            self.send_json({'images': files})

        elif path == '/api/colorscheme':
            cfg = read_json(CFG, {})
            self.send_json(cfg.get('colorScheme', {'accent': '#a855f7', 'accent2': '#d946ef'}))

        elif path == '/api/epk':
            cfg = read_json(CFG, {})
            epk_file = cfg.get('epkFile', '')
            self.send_json({
                'epkFile':         epk_file,
                'epkOriginalName': cfg.get('epkOriginalName', ''),
                'hasEpk':          bool(epk_file) and os.path.isfile(os.path.join(EPK_DIR, epk_file)),
            })

        elif path.startswith('/epk/'):
            filename = os.path.basename(path[5:])
            if not filename:
                self.send_error(400)
                return
            ext = os.path.splitext(filename)[1].lower()
            mime = EPK_MIME.get(ext, 'application/octet-stream')
            cfg = read_json(CFG, {})
            download_name = cfg.get('epkOriginalName') or filename
            self.send_download_file(os.path.join(EPK_DIR, filename), mime, download_name)

        else:
            self.send_error(404)

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        path = urlparse(self.path).path
        content_type = self.headers.get('Content-Type', '')

        # /api/contact stays open — it's the public site's own booking form.
        # Every other POST route edits site content or dashboard config.
        if path != '/api/contact' and not check_auth(self):
            require_auth(self)
            return

        # Audio file upload (multipart/form-data)
        if path.startswith('/api/audio/upload/'):
            try:
                idx = int(path.split('/')[-1])
            except ValueError:
                self.send_json({'error': 'Invalid track index'}, 400)
                return
            try:
                body = self.read_body_raw()
                filename, raw_filename, file_data = parse_multipart(content_type, body)
                if not filename or file_data is None:
                    self.send_json({'error': 'No file found in upload'}, 400)
                    return
                ext = os.path.splitext(filename)[1].lower()
                if ext not in AUDIO_MIME:
                    self.send_json({'error': f'Unsupported file type: {ext}'}, 400)
                    return
                save_path = os.path.join(AUDIO_DIR, filename)
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                raw = read_file(SITE)
                write_file(SITE, update_mix_audio(raw, idx, filename))
                self.send_json({'ok': True, 'filename': filename})
            except Exception as e:
                print(f'  [upload error] {e}')
                self.send_json({'error': str(e)}, 500)
            return

        # Image file upload (multipart/form-data)
        if path.startswith('/api/image/upload/'):
            target = path[len('/api/image/upload/'):]  # 'about' or 'service/0'
            try:
                body = self.read_body_raw()
                filename, raw_filename, file_data = parse_multipart(content_type, body)
                if not filename or file_data is None:
                    self.send_json({'error': 'No file found in upload'}, 400)
                    return
                ext = os.path.splitext(filename)[1].lower()
                if ext not in IMAGE_MIME:
                    self.send_json({'error': f'Unsupported image type: {ext}'}, 400)
                    return
                save_path = os.path.join(IMAGES_DIR, filename)
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                cfg = read_json(CFG, {})
                if not set_image_slot(cfg, target, filename):
                    self.send_json({'error': 'Unknown upload target'}, 400)
                    return
                write_json(CFG, cfg)
                self.send_json({'ok': True, 'filename': filename})
            except Exception as e:
                print(f'  [image upload error] {e}')
                self.send_json({'error': str(e)}, 500)
            return

        # Photo library upload (multipart/form-data) — adds to the gallery only
        if path == '/api/gallery/upload':
            try:
                body = self.read_body_raw()
                filename, raw_filename, file_data = parse_multipart(content_type, body)
                if not filename or file_data is None:
                    self.send_json({'error': 'No file found in upload'}, 400)
                    return
                ext = os.path.splitext(filename)[1].lower()
                if ext not in IMAGE_MIME:
                    self.send_json({'error': f'Unsupported image type: {ext}'}, 400)
                    return
                save_path = os.path.join(IMAGES_DIR, filename)
                if os.path.exists(save_path):
                    stem = os.path.splitext(filename)[0]
                    filename = f'{stem}-{int(datetime.now().timestamp())}{ext}'
                    save_path = os.path.join(IMAGES_DIR, filename)
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                self.send_json({'ok': True, 'filename': filename})
            except Exception as e:
                print(f'  [gallery upload error] {e}')
                self.send_json({'error': str(e)}, 500)
            return

        # EPK upload (multipart/form-data)
        if path == '/api/epk/upload':
            try:
                body = self.read_body_raw()
                filename, raw_filename, file_data = parse_multipart(content_type, body)
                if not filename or file_data is None:
                    self.send_json({'error': 'No file found in upload'}, 400)
                    return
                ext = os.path.splitext(filename)[1].lower()
                if ext not in EPK_MIME:
                    self.send_json({'error': f'Unsupported file type: {ext}'}, 400)
                    return
                cfg = read_json(CFG, {})
                old_file = cfg.get('epkFile', '')
                if old_file:
                    old_path = os.path.join(EPK_DIR, old_file)
                    if os.path.isfile(old_path):
                        os.remove(old_path)
                saved_name = f'EPK{ext}'
                save_path = os.path.join(EPK_DIR, saved_name)
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                cfg['epkFile'] = saved_name
                cfg['epkOriginalName'] = raw_filename
                write_json(CFG, cfg)
                self.send_json({'ok': True, 'filename': saved_name, 'originalName': raw_filename})
            except Exception as e:
                print(f'  [epk upload error] {e}')
                self.send_json({'error': str(e)}, 500)
            return

        # All other POST routes use JSON body
        try:
            data = self.read_body()
        except Exception:
            self.send_json({'error': 'Bad JSON'}, 400)
            return

        try:
            if path == '/api/about':
                raw = read_file(SITE)
                write_file(SITE, update_about(raw, data))
                self.send_json({'ok': True})

            elif path == '/api/services':
                raw = read_file(SITE)
                write_file(SITE, update_services(raw, data.get('services', [])))
                self.send_json({'ok': True})

            elif path == '/api/mixes':
                raw = read_file(SITE)
                write_file(SITE, update_mixes(raw, data.get('mixes', [])))
                self.send_json({'ok': True})

            elif path == '/api/genres':
                raw = read_file(SITE)
                write_file(SITE, update_genres(raw, data.get('genres', [])))
                self.send_json({'ok': True})

            elif path == '/api/availability':
                raw = read_file(SITE)
                write_file(SITE, update_booked_dates(raw, data.get('bookedDates', [])))
                self.send_json({'ok': True})

            elif path == '/api/socials':
                cfg = read_json(CFG, {})
                cfg['socials'] = data
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path == '/api/email-config':
                cfg = read_json(CFG, {})
                for key in ('emailUser', 'emailTo'):
                    if key in data:
                        cfg[key] = data[key]
                if data.get('emailPass'):
                    write_env_var('EMAIL_APP_PASSWORD', data['emailPass'])
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path == '/api/email-test':
                cfg = read_json(CFG, {})
                if not cfg.get('emailUser') or not get_email_pass():
                    self.send_json({'error': 'Email not configured.'}, 400)
                    return
                send_email(cfg, cfg.get('emailTo', cfg['emailUser']),
                           'DJ AB Dashboard — Test Email',
                           '<p style="font-family:sans-serif">This is a test email from your DJ AB dashboard. ✓ Email is working!</p>')
                self.send_json({'ok': True})

            elif path == '/api/contact':
                subs = read_json(SUBS, [])
                entry = {**data, 'id': int(datetime.now().timestamp() * 1000),
                         'receivedAt': datetime.utcnow().isoformat() + 'Z'}
                subs.insert(0, entry)
                write_json(SUBS, subs)
                cfg = read_json(CFG, {})
                if cfg.get('emailUser') and get_email_pass():
                    try:
                        to = cfg.get('emailTo', 'inquiries.djab@gmail.com')
                        subj = f"[Booking Inquiry] {data.get('event-type','Event')} — {data.get('name','')}"
                        send_email(cfg, to, subj, inquiry_html(data), reply_to=data.get('email'))
                    except Exception as e:
                        print(f'  [email error] {e}')
                self.send_json({'ok': True})

            elif path.startswith('/api/audio/remove/'):
                try:
                    idx = int(path.split('/')[-1])
                    raw = read_file(SITE)
                    write_file(SITE, update_mix_audio(raw, idx, ''))
                    self.send_json({'ok': True})
                except Exception as e:
                    self.send_json({'error': str(e)}, 500)

            elif path == '/api/image/remove/about':
                cfg = read_json(CFG, {})
                cfg['aboutImage'] = ''
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path.startswith('/api/image/remove/service/'):
                try:
                    idx = int(path.split('/')[-1])
                    cfg = read_json(CFG, {})
                    imgs = cfg.get('serviceImages', ['', '', '', ''])
                    while len(imgs) <= idx:
                        imgs.append('')
                    imgs[idx] = ''
                    cfg['serviceImages'] = imgs
                    write_json(CFG, cfg)
                    self.send_json({'ok': True})
                except Exception as e:
                    self.send_json({'error': str(e)}, 500)

            elif path == '/api/image/assign':
                target   = data.get('target', '')
                filename = data.get('filename', '')
                if filename and not os.path.isfile(os.path.join(IMAGES_DIR, os.path.basename(filename))):
                    self.send_json({'error': 'Photo not found in library'}, 400)
                    return
                cfg = read_json(CFG, {})
                if not set_image_slot(cfg, target, filename):
                    self.send_json({'error': 'Unknown target'}, 400)
                    return
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path == '/api/gallery/delete':
                filename = os.path.basename(data.get('filename', ''))
                if not filename:
                    self.send_json({'error': 'No filename given'}, 400)
                    return
                img_path = os.path.join(IMAGES_DIR, filename)
                if os.path.isfile(img_path):
                    os.remove(img_path)
                cfg = read_json(CFG, {})
                if cfg.get('aboutImage') == filename:
                    cfg['aboutImage'] = ''
                if cfg.get('headerImage') == filename:
                    cfg['headerImage'] = ''
                if cfg.get('bannerImage') == filename:
                    cfg['bannerImage'] = ''
                cfg['serviceImages'] = ['' if f == filename else f for f in cfg.get('serviceImages', ['', '', '', ''])]
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path == '/api/colorscheme':
                cfg = read_json(CFG, {})
                old_scheme = cfg.get('colorScheme', {'accent': '#a855f7', 'accent2': '#d946ef'})
                new_scheme = {
                    'accent':  data.get('accent',  old_scheme.get('accent')),
                    'accent2': data.get('accent2', old_scheme.get('accent2')),
                }
                raw = read_file(SITE)
                write_file(SITE, update_color_scheme(raw, old_scheme, new_scheme))
                cfg['colorScheme'] = new_scheme
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            elif path == '/api/epk/remove':
                cfg = read_json(CFG, {})
                old_file = cfg.get('epkFile', '')
                if old_file:
                    old_path = os.path.join(EPK_DIR, old_file)
                    if os.path.isfile(old_path):
                        os.remove(old_path)
                cfg['epkFile'] = ''
                cfg['epkOriginalName'] = ''
                write_json(CFG, cfg)
                self.send_json({'ok': True})

            else:
                self.send_error(404)

        except Exception as e:
            print(f'  [error] {e}')
            self.send_json({'error': str(e)}, 500)

    # ── DELETE ────────────────────────────────────────────────────────────────

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not check_auth(self):
            require_auth(self)
            return
        if path.startswith('/api/submissions/'):
            try:
                sid = int(path.split('/')[-1])
                subs = [s for s in read_json(SUBS, []) if s.get('id') != sid]
                write_json(SUBS, subs)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
        else:
            self.send_error(404)

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    os.makedirs(EPK_DIR, exist_ok=True)

    print('\n  ╔══════════════════════════════════════╗')
    print('  ║      DJ AB Dashboard  •  Running     ║')
    print('  ╚══════════════════════════════════════╝\n')
    print(f'  Website   →  http://localhost:{PORT}/')
    print(f'  Dashboard →  http://localhost:{PORT}/dashboard\n')
    if not DASHBOARD_PASS:
        print('  ⚠  DASHBOARD_PASSWORD is not set — /dashboard and all admin')
        print('     API routes will refuse every request until you set')
        print('     DASHBOARD_USERNAME / DASHBOARD_PASSWORD in a local .env')
        print('     file (copy .env.example to .env to get started).\n')
    if not get_email_pass():
        print('  ⚠  EMAIL_APP_PASSWORD is not set — booking-inquiry emails')
        print('     will not send until it is set in your local .env file.\n')
    print('  Press Ctrl+C to stop.\n')

    server = HTTPServer(('', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')
