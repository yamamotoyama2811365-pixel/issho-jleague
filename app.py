import os
import random
import re
import uuid
from league_content import build_club_hub
from club_hub import build_sapporo_hub
from club_goods import build_club_goods
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request, abort
from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, Text, select, func, desc, asc, or_, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, joinedload

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///issho_jleague.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Club(Base):
    __tablename__ = "clubs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stadium_id: Mapped[int | None] = mapped_column(ForeignKey("stadiums.id"), nullable=True)
    players: Mapped[list["Player"]] = relationship(back_populates="club", cascade="all, delete-orphan")
    stadium: Mapped["Stadium | None"] = relationship(back_populates="clubs")


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birthplace: Mapped[str | None] = mapped_column(String(80), nullable=True)
    birthdate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    appearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    club: Mapped[Club] = relationship(back_populates="players")


class Stadium(Base):
    __tablename__ = "stadiums"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    gourmet_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    hotel_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sightseeing_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    seat_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clubs: Mapped[list[Club]] = relationship(back_populates="stadium")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    question: Mapped[str] = mapped_column(Text)
    option_a: Mapped[str] = mapped_column(String(255))
    option_b: Mapped[str] = mapped_column(String(255))
    option_c: Mapped[str] = mapped_column(String(255))
    option_d: Mapped[str] = mapped_column(String(255))
    correct: Mapped[str] = mapped_column(String(1))
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_ids: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class PlayerSocial(Base):
    __tablename__ = "player_socials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    url: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["TRUST_RENDER_PROXY"] = os.getenv("RENDER") == "true"

CLUBS = {
    "J1": [
        ("鹿島アントラーズ", "kashima"), ("水戸ホーリーホック", "mito"), ("浦和レッズ", "urawa"), ("ジェフユナイテッド千葉", "chiba"),
        ("柏レイソル", "kashiwa"), ("ＦＣ東京", "ftokyo"), ("東京ヴェルディ", "tokyo-v"), ("ＦＣ町田ゼルビア", "machida"),
        ("川崎フロンターレ", "kawasaki"), ("横浜Ｆ・マリノス", "yokohama-fm"), ("清水エスパルス", "shimizu"), ("名古屋グランパス", "nagoya"),
        ("京都サンガF.C.", "kyoto"), ("ガンバ大阪", "g-osaka"), ("セレッソ大阪", "c-osaka"), ("ヴィッセル神戸", "kobe"),
        ("ファジアーノ岡山", "okayama"), ("サンフレッチェ広島", "hiroshima"), ("アビスパ福岡", "fukuoka"), ("Ｖ・ファーレン長崎", "nagasaki")
    ],
    "J2": [
        ("北海道コンサドーレ札幌", "sapporo"), ("ヴァンラーレ八戸", "hachinohe"), ("ベガルタ仙台", "sendai"), ("ブラウブリッツ秋田", "akita"),
        ("モンテディオ山形", "yamagata"), ("いわきＦＣ", "iwaki"), ("栃木シティ", "tochigi-c"), ("ＲＢ大宮アルディージャ", "omiya"),
        ("横浜ＦＣ", "yokohama-fc"), ("湘南ベルマーレ", "shonan"), ("ヴァンフォーレ甲府", "kofu"), ("アルビレックス新潟", "niigata"),
        ("カターレ富山", "toyama"), ("ジュビロ磐田", "iwata"), ("藤枝ＭＹＦＣ", "fujieda"), ("徳島ヴォルティス", "tokushima"),
        ("ＦＣ今治", "imabari"), ("サガン鳥栖", "tosu"), ("大分トリニータ", "oita"), ("テゲバジャーロ宮崎", "miyazaki")
    ],
    "J3": [
        ("福島ユナイテッドＦＣ", "fukushima"), ("栃木ＳＣ", "tochigi"), ("ザスパ群馬", "gunma"), ("ＳＣ相模原", "sagamihara"),
        ("松本山雅ＦＣ", "matsumoto"), ("ＡＣ長野パルセイロ", "nagano"), ("ツエーゲン金沢", "kanazawa"), ("ＦＣ岐阜", "gifu"),
        ("レイラック滋賀ＦＣ", "shiga"), ("ＦＣ大阪", "fc-osaka"), ("奈良クラブ", "nara"), ("ガイナーレ鳥取", "tottori"),
        ("レノファ山口ＦＣ", "yamaguchi"), ("カマタマーレ讃岐", "sanuki"), ("愛媛ＦＣ", "ehime"), ("高知ユナイテッドＳＣ", "kochi"),
        ("ギラヴァンツ北九州", "kitakyushu"), ("ロアッソ熊本", "kumamoto"), ("鹿児島ユナイテッドＦＣ", "kagoshima"), ("ＦＣ琉球", "ryukyu")
    ]
}

QUIZ_SEED = [
    ("club", "2026/27シーズンのJ1所属クラブはどれ？", ["鹿島アントラーズ", "北海道コンサドーレ札幌", "ジュビロ磐田", "ロアッソ熊本"], "A", "2026/27の鹿島はJ1所属。"),
    ("club", "2026/27シーズンのJ2所属クラブはどれ？", ["北海道コンサドーレ札幌", "柏レイソル", "ＦＣ町田ゼルビア", "栃木ＳＣ"], "A", "札幌は2026/27シーズンJ2所属。"),
    ("club", "2026/27シーズンのJ3所属クラブはどれ？", ["ツエーゲン金沢", "アルビレックス新潟", "湘南ベルマーレ", "アビスパ福岡"], "A", "金沢は2026/27シーズンJ3所属。"),
    ("stadium", "北海道コンサドーレ札幌のホームスタジアムとしてJリーグ公式に掲載されているのは？", ["大和ハウス プレミストドーム", "味の素スタジアム", "ノエビアスタジアム神戸", "駅前不動産スタジアム"], "A", "札幌のホームスタジアムは大和ハウス プレミストドーム。"),
    ("club", "2026/27シーズンにJ1へ所属する長崎県のクラブは？", ["Ｖ・ファーレン長崎", "ロアッソ熊本", "サガン鳥栖", "大分トリニータ"], "A", "Ｖ・ファーレン長崎がJ1所属。"),
    ("club", "2026/27シーズンにJ2へ所属する新潟県のクラブは？", ["アルビレックス新潟", "カターレ富山", "ツエーゲン金沢", "松本山雅ＦＣ"], "A", "アルビレックス新潟がJ2所属。"),
    ("club", "2026/27シーズンにJ3へ所属する滋賀県のクラブは？", ["レイラック滋賀ＦＣ", "奈良クラブ", "ＦＣ大阪", "ＦＣ岐阜"], "A", "レイラック滋賀FCがJ3所属。"),
    ("stadium", "2026/27 J1の日程でガンバ大阪のホーム開催に使われている略称「パナスタ」はどのスタジアム？", ["パナソニック スタジアム 吹田", "豊田スタジアム", "日産スタジアム", "埼玉スタジアム2002"], "A", "パナスタはパナソニック スタジアム 吹田の略称。"),
    ("stadium", "2026/27 J1の日程でサンフレッチェ広島のホーム開催に使われる「Eピース」は？", ["エディオンピースウイング広島", "ピーススタジアム Connected by SoftBank", "ベスト電器スタジアム", "JFE晴れの国スタジアム"], "A", "Eピースはエディオンピースウイング広島。"),
    ("club", "2026/27シーズンにJ1所属の岡山県クラブは？", ["ファジアーノ岡山", "レノファ山口ＦＣ", "徳島ヴォルティス", "愛媛ＦＣ"], "A", "ファジアーノ岡山がJ1所属。"),
    ("club", "2026/27シーズンにJ2所属の栃木県クラブは？", ["栃木シティ", "栃木ＳＣ", "ザスパ群馬", "水戸ホーリーホック"], "A", "栃木シティがJ2所属。"),
    ("club", "2026/27シーズンにJ3所属の栃木県クラブは？", ["栃木ＳＣ", "栃木シティ", "水戸ホーリーホック", "ザスパ群馬"], "A", "栃木SCがJ3所属。"),
    ("club", "2026/27シーズンにJ1所属の千葉県クラブは？", ["ジェフユナイテッド千葉", "ＲＢ大宮アルディージャ", "横浜ＦＣ", "湘南ベルマーレ"], "A", "ジェフユナイテッド千葉がJ1所属。"),
    ("club", "2026/27シーズンにJ2所属の鳥栖市のクラブは？", ["サガン鳥栖", "アビスパ福岡", "ギラヴァンツ北九州", "大分トリニータ"], "A", "サガン鳥栖がJ2所属。"),
    ("stadium", "2026/27 J1日程で横浜F・マリノスのホーム開催に使われる「日産ス」は？", ["日産スタジアム", "ニッパツ三ツ沢球技場", "味の素スタジアム", "国立競技場"], "A", "日産スは日産スタジアムの略称。"),
]


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Club)) == 0:
            for league, clubs in CLUBS.items():
                for name, slug in clubs:
                    db.add(Club(league=league, name=name, slug=slug, source_url=f"https://www.jleague.jp/club/{slug}/player/"))
        if db.scalar(select(func.count()).select_from(QuizQuestion)) == 0:
            for cat, q, opts, correct, exp in QUIZ_SEED:
                db.add(QuizQuestion(category=cat, question=q, option_a=opts[0], option_b=opts[1], option_c=opts[2], option_d=opts[3], correct=correct, explanation=exp))
        db.commit()


def safe_rows(sql, params=None):
    try:
        with engine.begin() as conn:
            return [dict(row._mapping) for row in conn.execute(text(sql), params or {})]
    except Exception:
        return []


def standing_for(club_id):
    rows = safe_rows("""
        SELECT s.*, c.name AS club_name, c.slug AS club_slug
        FROM standings s JOIN clubs c ON c.id = s.club_id
        WHERE s.club_id = :club_id
        LIMIT 1
    """, {"club_id": club_id})
    return rows[0] if rows else None


def results_for(club_id, limit=12):
    return safe_rows("""
        SELECT cr.*, c.name AS club_name, c.slug AS club_slug, c.league
        FROM club_results cr JOIN clubs c ON c.id = cr.club_id
        WHERE cr.club_id = :club_id
        ORDER BY cr.match_date DESC, cr.id DESC
        LIMIT :limit
    """, {"club_id": club_id, "limit": limit})


def fixtures_for(club_id=None, stadium_id=None, limit=20, league=None):
    where = ["f.match_date >= :today", "f.source_version = 2"]
    params = {"limit": limit, "today": datetime.now(timezone(timedelta(hours=9))).date()}
    if club_id is not None:
        where.append("(f.home_club_id = :club_id OR f.away_club_id = :club_id)")
        params["club_id"] = club_id
    if stadium_id is not None:
        where.append("f.stadium_id = :stadium_id")
        params["stadium_id"] = stadium_id
    if league in {"J1", "J2", "J3"}:
        where.append("f.league = :league")
        params["league"] = league
    return safe_rows(f"""
        SELECT f.*,
               hc.slug AS home_slug,
               ac.slug AS away_slug,
               s.slug AS stadium_slug
        FROM fixtures f
        LEFT JOIN clubs hc ON hc.id = f.home_club_id
        LEFT JOIN clubs ac ON ac.id = f.away_club_id
        LEFT JOIN stadiums s ON s.id = f.stadium_id
        WHERE {' AND '.join(where)}
        ORDER BY f.match_date, f.kickoff NULLS LAST, f.match_key
        {'LIMIT :limit' if limit is not None else ''}
    """, params)


def next_rounds_for(fixtures):
    """Select a whole round per league, rather than slicing the first N games."""
    out = {}
    for league in ("J1", "J2", "J3"):
        upcoming = [f for f in fixtures if f["league"] == league]
        first = upcoming[0] if upcoming else None
        matches = [f for f in upcoming if f.get("round_label") == first.get("round_label")] if first else []
        out[league] = {"label": first.get("round_label") if first else None, "matches": matches}
    return out


def socials_for(player_id):
    return safe_rows("""
        SELECT platform, url, handle, source_url, checked_at
        FROM player_socials
        WHERE player_id = :player_id
        ORDER BY CASE platform
            WHEN 'Instagram' THEN 1
            WHEN 'X' THEN 2
            WHEN 'TikTok' THEN 3
            WHEN 'YouTube' THEN 4
            ELSE 9 END
    """, {"player_id": player_id})


@app.context_processor
def inject_globals():
    return {"current_year": datetime.now().year}


@app.route("/")
def home():
    with SessionLocal() as db:
        club_count = db.scalar(select(func.count()).select_from(Club)) or 0
        player_count = db.scalar(select(func.count()).select_from(Player)) or 0
        stadium_count = db.scalar(select(func.count()).select_from(Stadium)) or 0
        top = db.scalars(select(QuizAttempt).where(QuizAttempt.completed_at.is_not(None)).order_by(desc(QuizAttempt.score), asc(QuizAttempt.elapsed_ms)).limit(5)).all()
        top_scorers = db.scalars(
            select(Player).options(joinedload(Player.club))
            .where(Player.goals.is_not(None))
            .order_by(desc(Player.goals), desc(Player.appearances), Player.name)
            .limit(8)
        ).all()

    standings = {}
    for league in ("J1", "J2", "J3"):
        standings[league] = safe_rows("""
            SELECT s.rank, s.points, s.played, s.wins, s.draws, s.losses,
                   s.goals_for, s.goals_against, s.goal_diff, s.recent_form,
                   c.name, c.slug
            FROM standings s JOIN clubs c ON c.id = s.club_id
            WHERE s.league = :league
            ORDER BY s.rank
        """, {"league": league})

    recent_results = safe_rows("""
        SELECT cr.match_date, cr.kickoff, cr.opponent, cr.venue, cr.result,
               cr.club_score, cr.opponent_score, cr.competition, cr.attendance,
               c.name AS club_name, c.slug AS club_slug, c.league
        FROM club_results cr JOIN clubs c ON c.id = cr.club_id
        ORDER BY cr.match_date DESC, cr.id DESC
        LIMIT 12
    """)

    return render_template(
        "index.html",
        club_count=club_count,
        player_count=player_count,
        stadium_count=stadium_count,
        top=top,
        standings=standings,
        recent_results=recent_results,
        top_scorers=top_scorers,
        next_rounds=next_rounds_for(fixtures_for(limit=None)),
    )


@app.route("/players")
def players():
    q = (request.args.get("q") or "").strip()
    league = (request.args.get("league") or "").strip().upper()
    club_slug = (request.args.get("club") or "").strip()
    with SessionLocal() as db:
        stmt = select(Player).options(joinedload(Player.club)).join(Club)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Player.name.ilike(like), Club.name.ilike(like), Player.birthplace.ilike(like)))
        if league in {"J1", "J2", "J3"}:
            stmt = stmt.where(Club.league == league)
        if club_slug:
            stmt = stmt.where(Club.slug == club_slug)
        stmt = stmt.order_by(Club.league, Club.name, Player.position, Player.number.nulls_last(), Player.name).limit(5000)
        rows = db.scalars(stmt).all()
        clubs = db.scalars(select(Club).order_by(Club.league, Club.name)).all()
        total = db.scalar(select(func.count()).select_from(Player)) or 0
    return render_template("players.html", players=rows, clubs=clubs, total=total, q=q, league=league, club_slug=club_slug)


@app.route("/player/<slug>")
def player_detail(slug):
    with SessionLocal() as db:
        p = db.scalar(select(Player).options(joinedload(Player.club)).where(Player.slug == slug))
        if not p:
            abort(404)
        teammates = db.scalars(
            select(Player)
            .where(Player.club_id == p.club_id, Player.id != p.id)
            .order_by(desc(Player.goals), Player.position, Player.number.nulls_last())
            .limit(8)
        ).all()
        club = db.scalar(select(Club).options(joinedload(Club.stadium)).where(Club.id == p.club_id))
    return render_template(
        "player.html",
        player=p,
        club=club,
        standing=standing_for(p.club_id),
        recent_results=results_for(p.club_id, 5),
        teammates=teammates,
        socials=socials_for(p.id),
        next_fixtures=fixtures_for(club_id=p.club_id, limit=3),
    )


@app.route("/clubs")
def clubs():
    with SessionLocal() as db:
        rows = db.scalars(select(Club).options(joinedload(Club.stadium)).order_by(Club.league, Club.name)).unique().all()
    standing_rows = safe_rows("SELECT club_id, rank, points, played, wins, draws, losses, goal_diff FROM standings")
    standing_map = {r["club_id"]: r for r in standing_rows}
    rows.sort(key=lambda c: (c.league, standing_map.get(c.id, {}).get("rank") or 999, c.name))
    next_map = {}
    for fixture in fixtures_for(limit=None):
        for cid in (fixture["home_club_id"], fixture["away_club_id"]):
            next_map.setdefault(cid, fixture)
    return render_template("clubs.html", clubs=rows, standing_map=standing_map, next_map=next_map)


@app.route("/club/<slug>")
def club_detail(slug):
    with SessionLocal() as db:
        club = db.scalar(select(Club).options(joinedload(Club.stadium)).where(Club.slug == slug))
        if not club:
            abort(404)
        roster = db.scalars(
            select(Player)
            .where(Player.club_id == club.id)
            .order_by(Player.position, Player.number.nulls_last(), Player.name)
        ).all()
        top_scorers = db.scalars(
            select(Player)
            .where(Player.club_id == club.id, Player.goals.is_not(None))
            .order_by(desc(Player.goals), desc(Player.appearances), Player.name)
            .limit(6)
        ).all()
    if slug == "sapporo":
        standing = standing_for(club.id)
        recent_results = results_for(club.id, 40)
        league_rows = safe_rows("""
            SELECT s.*, c.name, c.slug FROM standings s JOIN clubs c ON c.id = s.club_id
            WHERE s.league = :league ORDER BY s.rank
        """, {"league": club.league})
        hub = build_sapporo_hub(standing, recent_results, league_rows, roster)
        return render_template("club_sapporo.html", club=club, roster=roster,
                               top_scorers=top_scorers, standing=standing, hub=hub, goods=build_club_goods(slug))
    return render_template(
        "club.html",
        hub=build_club_hub(club, roster),
        goods=build_club_goods(slug),
        club=club,
        roster=roster,
        top_scorers=top_scorers,
        standing=standing_for(club.id),
        recent_results=results_for(club.id, 12),
        next_fixtures=fixtures_for(club_id=club.id, limit=5),
    )


@app.route("/schedule")
def schedule_page():
    with SessionLocal() as db:
        clubs = db.scalars(select(Club).order_by(Club.league, Club.name)).all()
    rows = fixtures_for(limit=None)
    return render_template("schedule.html", fixtures=rows, clubs=clubs,
                           next_rounds=next_rounds_for(rows))


@app.route("/standings")
def standings_page():
    leagues = {}
    for league in ("J1", "J2", "J3"):
        leagues[league] = safe_rows("""
            SELECT s.*, c.name, c.slug
            FROM standings s JOIN clubs c ON c.id = s.club_id
            WHERE s.league = :league
            ORDER BY s.rank
        """, {"league": league})
    return render_template("standings.html", leagues=leagues)


@app.route("/results")
def results_page():
    league = (request.args.get("league") or "").upper()
    club_slug = (request.args.get("club") or "").strip()
    where = []
    params = {"limit": 120}
    if league in {"J1", "J2", "J3"}:
        where.append("c.league = :league")
        params["league"] = league
    if club_slug:
        where.append("c.slug = :club_slug")
        params["club_slug"] = club_slug
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    rows = safe_rows(f"""
        SELECT cr.*, c.name AS club_name, c.slug AS club_slug, c.league
        FROM club_results cr JOIN clubs c ON c.id = cr.club_id
        {where_sql}
        ORDER BY cr.match_date DESC, cr.id DESC
        {'' if app.config.get('STATIC_EXPORT') else 'LIMIT :limit'}
    """, params)
    with SessionLocal() as db:
        clubs = db.scalars(select(Club).order_by(Club.league, Club.name)).all()
    return render_template("results.html", rows=rows, clubs=clubs, league=league, club_slug=club_slug)


@app.route("/stadiums")
def stadiums():
    with SessionLocal() as db:
        rows = db.scalars(select(Stadium).options(joinedload(Stadium.clubs)).order_by(Stadium.name)).unique().all()
    return render_template("stadiums.html", stadiums=rows)


@app.route("/stadium/<slug>")
def stadium_detail(slug):
    with SessionLocal() as db:
        s = db.scalar(select(Stadium).options(joinedload(Stadium.clubs)).where(Stadium.slug == slug))
        if not s:
            abort(404)
        club_ids = [c.id for c in s.clubs]
    club_cards = []
    for club_id in club_ids:
        club_cards.append({"standing": standing_for(club_id), "results": results_for(club_id, 3)})
    return render_template(
        "stadium.html",
        stadium=s,
        club_cards=club_cards,
        next_fixtures=fixtures_for(stadium_id=s.id, limit=8),
    )


@app.route("/quiz")
def quiz_page():
    return render_template("quiz.html")


@app.post("/api/quiz/start")
def quiz_start():
    with SessionLocal() as db:
        all_q = db.scalars(select(QuizQuestion)).all()
        if len(all_q) < 10:
            return jsonify({"error": "クイズ問題が10問未満です"}), 500
        chosen = random.sample(all_q, 10)
        payload = []
        attempt_tokens = []
        for q in chosen:
            original = {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d}
            correct_text = original[q.correct]
            shuffled = list(original.values())
            random.shuffle(shuffled)
            options = {chr(65 + i): value for i, value in enumerate(shuffled)}
            correct_value = next(k for k, value in options.items() if value == correct_text)
            attempt_tokens.append(f"{q.id}:{correct_value}")
            payload.append({"id": q.id, "question": q.question, "category": q.category, "options": options})
        attempt = QuizAttempt(id=str(uuid.uuid4()), question_ids=",".join(attempt_tokens))
        db.add(attempt)
        db.commit()
        return jsonify({"attempt_id": attempt.id, "questions": payload})


@app.post("/api/quiz/submit")
def quiz_submit():
    data = request.get_json(silent=True) or {}
    attempt_id = str(data.get("attempt_id") or "")
    nickname = re.sub(r"\s+", " ", str(data.get("nickname") or "").strip())[:24]
    answers = data.get("answers") or {}
    if not nickname:
        return jsonify({"error": "ニックネームを入力してください"}), 400
    if not isinstance(answers, dict):
        return jsonify({"error": "回答形式が不正です"}), 400
    with SessionLocal() as db:
        attempt = db.get(QuizAttempt, attempt_id)
        if not attempt:
            return jsonify({"error": "挑戦データが見つかりません"}), 404
        if attempt.completed_at is not None:
            return jsonify({"error": "この挑戦は送信済みです"}), 409
        tokens = [x for x in attempt.question_ids.split(",") if x]
        ids = []
        attempt_keys = {}
        for token in tokens:
            if ":" in token:
                raw_id, key = token.split(":", 1)
                qid = int(raw_id)
                attempt_keys[qid] = key.upper()
            else:
                qid = int(token)
            ids.append(qid)
        qs = db.scalars(select(QuizQuestion).where(QuizQuestion.id.in_(ids))).all()
        qmap = {q.id: q for q in qs}
        score = 0
        details = []
        for qid in ids:
            q = qmap.get(qid)
            ans = str(answers.get(str(qid), "")).upper()
            correct_value = attempt_keys.get(qid, q.correct if q else "")
            ok = bool(q and ans == correct_value)
            score += int(ok)
            details.append({"id": qid, "correct": ok, "correct_value": correct_value if q else None, "explanation": q.explanation if q else None})
        now = datetime.now(timezone.utc)
        started = attempt.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_ms = max(0, min(int((now - started).total_seconds() * 1000), 60 * 60 * 1000))
        attempt.completed_at = now
        attempt.nickname = nickname
        attempt.score = score
        attempt.elapsed_ms = elapsed_ms
        db.commit()
        rank = ranking_for_attempt(db, attempt)
        return jsonify({"score": score, "total": len(ids), "elapsed_ms": elapsed_ms, "rank": rank, "details": details})


def ranking_for_attempt(db, attempt):
    better = db.scalar(select(func.count()).select_from(QuizAttempt).where(
        QuizAttempt.completed_at.is_not(None),
        or_(QuizAttempt.score > attempt.score, (QuizAttempt.score == attempt.score) & (QuizAttempt.elapsed_ms < attempt.elapsed_ms))
    )) or 0
    return better + 1


@app.route("/leaderboard")
def leaderboard():
    period = request.args.get("period", "all")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        stmt = select(QuizAttempt).where(QuizAttempt.completed_at.is_not(None))
        if period == "today":
            start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
            stmt = stmt.where(QuizAttempt.completed_at >= start)
        elif period == "week":
            stmt = stmt.where(QuizAttempt.completed_at >= now - timedelta(days=7))
        rows = db.scalars(stmt.order_by(desc(QuizAttempt.score), asc(QuizAttempt.elapsed_ms), asc(QuizAttempt.completed_at)).limit(100)).all()
    return render_template("leaderboard.html", rows=rows, period=period)


@app.route("/health")
def health():
    return jsonify({"ok": True})


from fan_community import register as register_fan_community
register_fan_community(app, engine, CLUBS)



@app.route('/about')
def about_page():
    return render_template('about.html')

@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=os.getenv("FLASK_DEBUG") == "1")
else:
    init_db()
