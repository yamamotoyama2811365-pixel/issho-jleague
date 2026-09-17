"""Small, moderated fan features. Public APIs never expose pending messages or counts."""
import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone, timedelta
from flask import jsonify, request
from sqlalchemy import MetaData, Table, Column, String, Integer, Text, select, func, delete, update, text
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
views = Table('fan_views', metadata, Column('id', String(64), primary_key=True), Column('club', String(80), nullable=False, index=True), Column('created_at', Integer, nullable=False, index=True))
messages = Table('fan_messages', metadata,
    Column('id', String(32), primary_key=True), Column('club', String(80), nullable=False, index=True),
    Column('nickname', String(12), nullable=False), Column('body', String(20), nullable=False),
    Column('status', String(16), nullable=False, index=True), Column('created_at', Integer, nullable=False, index=True),
    Column('reviewed_at', Integer), Column('delete_hash', String(64), nullable=False), Column('report_reason', String(32)))
limits = Table('fan_limits', metadata, Column('id', String(64), primary_key=True), Column('created_at', Integer, nullable=False, index=True))
settings = Table('fan_settings', metadata, Column('key', String(32), primary_key=True), Column('value', Text, nullable=False))
ORIGINS = {'https://issho-jleague.pages.dev', 'https://issho-jleague.onrender.com'}
REPORT_REASONS = {'abuse', 'personal', 'spam', 'other'}
JST = timezone(timedelta(hours=9))


def initialize(engine):
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(74213650)'))
        metadata.create_all(conn)
        key = conn.scalar(select(settings.c.value).where(settings.c.key == 'hash_secret'))
        if not key:
            key = secrets.token_hex(32)
            conn.execute(settings.insert().values(key='hash_secret', value=key))
    return key.encode()


def clean_text(value, maximum):
    if not isinstance(value, str):
        raise ValueError('文字を入力してください。')
    value = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(value) <= maximum:
        raise ValueError(f'1〜{maximum}文字で入力してください。絵文字は複数文字として数える場合があります。')
    normalized = unicodedata.normalize('NFKC', value).lower()
    if any(unicodedata.category(c).startswith('C') for c in value) or re.search(r'[<>@]|https?://|www\.|\d{7,}', normalized):
        raise ValueError('URL・連絡先・特殊な制御文字は掲載できません。')
    if any(word in normalized for word in ('死ね', 'しね', '殺す', 'ころす', '消えろ', 'きえろ')):
        raise ValueError('チームへの応援の言葉をお願いします。')
    return value


def rankings(engine, clubs, now=None):
    now = int(time.time()) if now is None else now
    start = now - 7 * 86400
    with engine.connect() as conn:
        counts = dict(conn.execute(select(views.c.club, func.count()).where(views.c.created_at >= start).group_by(views.c.club)).all())
    leagues = {}
    for league, entries in clubs.items():
        ordered = sorted(entries, key=lambda c: (-counts.get(c[1], 0), c[0]))
        frequencies = Counter(counts.get(slug, 0) for _, slug in ordered)
        result = []
        previous, rank = None, None
        for index, (name, slug) in enumerate(ordered, 1):
            count = counts.get(slug, 0)
            if count != previous:
                rank = index if count else None
            result.append(dict(slug=slug, name=name, rank=rank, tied=bool(count and frequencies[count] > 1)))
            previous = count
        leagues[league] = result
    return dict(leagues=leagues, updated_at=datetime.fromtimestamp(now, JST).strftime('%Y/%m/%d %H:%M'),
                period='過去7日間', starts_at=datetime.fromtimestamp(start, JST).strftime('%Y/%m/%d'), version=1)


def cleanup(engine, now=None):
    now = int(time.time()) if now is None else now
    with engine.begin() as conn:
        conn.execute(delete(views).where(views.c.created_at < now - 8 * 86400))
        conn.execute(delete(limits).where(limits.c.created_at < now - 2 * 86400))
        conn.execute(delete(messages).where(messages.c.status != 'approved', messages.c.created_at < now - 30 * 86400))
        conn.execute(delete(messages).where(messages.c.created_at < now - 180 * 86400))


def register(app, engine, clubs):
    secret = initialize(engine)
    slugs = {slug for group in clubs.values() for _, slug in group}

    def fingerprint(purpose):
        # Render's final forwarding hop is appended by its ingress; never trust the first client-supplied hop.
        forwarded = request.headers.get('X-Forwarded-For', '').split(',')[-1].strip() if app.config.get('TRUST_RENDER_PROXY') else ''
        address = forwarded or request.remote_addr or 'unknown'
        return hmac.new(secret, (purpose + ':' + address).encode(), hashlib.sha256).hexdigest()

    def limited(conn, purpose, seconds):
        bucket = int(time.time()) // seconds
        key = fingerprint(f'{purpose}:{bucket}')
        conn.execute(limits.insert().values(id=key, created_at=int(time.time())))

    @app.before_request
    def fan_guard():
        if not request.path.startswith('/api/fan/'):
            return None
        if request.method == 'OPTIONS':
            return ('', 204)
        if request.method == 'POST':
            if request.headers.get('Origin') not in ORIGINS:
                return jsonify(error='このサイトの画面から送信してください。'), 403
            if (request.content_length or 0) > 2048 or not request.is_json:
                return jsonify(error='送信形式を確認してください。'), 400
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='送信内容を確認してください。'), 400

    @app.after_request
    def fan_headers(response):
        if request.path.startswith('/api/fan/'):
            if request.headers.get('Origin') in ORIGINS:
                response.headers['Access-Control-Allow-Origin'] = request.headers['Origin']
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
                response.headers.add('Vary', 'Origin')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/api/fan/status')
    def fan_status():
        return jsonify(ok=True, version=1, message_clubs=['sapporo'])

    @app.post('/api/fan/view')
    def fan_view():
        payload = request.get_json()
        club = payload.get('club')
        if not isinstance(club, str) or club not in slugs:
            return jsonify(error='クラブが見つかりません。'), 400
        if re.search(r'bot|crawler|spider|headless|preview', request.user_agent.string, re.I):
            return ('', 204)
        now = int(time.time())
        key = fingerprint(f'view:{club}:{now // 1800}')
        try:
            with engine.begin() as conn:
                # A single connection cannot contribute to every club in a short burst.
                limited(conn, 'view-burst', 5)
                conn.execute(views.insert().values(id=key, club=club, created_at=now))
        except IntegrityError:
            pass
        return ('', 204)

    @app.get('/api/fan/rankings')
    def fan_rankings():
        return jsonify(rankings(engine, clubs))

    @app.get('/api/fan/messages/<club>')
    def fan_messages(club):
        if club != 'sapporo':
            return jsonify(error='このクラブの受付は準備中です。'), 404
        with engine.connect() as conn:
            rows = conn.execute(select(messages.c.id, messages.c.nickname, messages.c.body, messages.c.created_at)
                .where(messages.c.club == club, messages.c.status == 'approved')
                .order_by(messages.c.created_at.desc(), messages.c.id).limit(30)).mappings().all()
        return jsonify(messages=[dict(row) for row in rows])

    @app.post('/api/fan/messages/sapporo')
    def fan_submit():
        payload = request.get_json()
        if payload.get('website') or payload.get('agree') is not True:
            return jsonify(error='投稿ルールを確認してください。'), 400
        try:
            nickname = clean_text(payload.get('nickname'), 12)
            body = clean_text(payload.get('body'), 20)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        mid, delete_token = secrets.token_hex(16), secrets.token_urlsafe(32)
        try:
            with engine.begin() as conn:
                limited(conn, 'message', 1800)
                conn.execute(messages.insert().values(id=mid, club='sapporo', nickname=nickname, body=body,
                    status='pending', created_at=int(time.time()), delete_hash=hashlib.sha256(delete_token.encode()).hexdigest()))
        except IntegrityError:
            return jsonify(error='連続投稿を防ぐため、30分ほど時間を空けてください。'), 429
        return jsonify(id=mid, delete_token=delete_token, status='pending', message='応援を受け付けました。確認後に公開します。'), 201

    @app.post('/api/fan/messages/<mid>/delete')
    def fan_delete(mid):
        token = request.get_json().get('delete_token')
        if not isinstance(token, str) or len(token) > 100:
            return jsonify(error='削除情報を確認できません。'), 403
        digest = hashlib.sha256(token.encode()).hexdigest()
        with engine.begin() as conn:
            row = conn.execute(select(messages.c.delete_hash).where(messages.c.id == mid)).first()
            if not row or not hmac.compare_digest(row[0], digest):
                return jsonify(error='削除情報を確認できません。'), 403
            conn.execute(delete(messages).where(messages.c.id == mid))
        return jsonify(ok=True)

    @app.post('/api/fan/messages/<mid>/report')
    def fan_report(mid):
        reason = request.get_json().get('reason')
        if not isinstance(reason, str) or reason not in REPORT_REASONS:
            return jsonify(error='通報理由を選んでください。'), 400
        try:
            with engine.begin() as conn:
                limited(conn, 'report', 60)
                row = conn.execute(update(messages).where(messages.c.id == mid, messages.c.status == 'approved')
                    .values(status='reported', report_reason=reason))
                if not row.rowcount:
                    return jsonify(error='公開中のメッセージが見つかりません。'), 404
        except IntegrityError:
            return jsonify(error='時間を空けてお試しください。'), 429
        return jsonify(ok=True, message='一時非表示にして確認します。')
