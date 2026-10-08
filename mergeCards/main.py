# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) игры "mergeCards" на PyQt5.

Игровая логика — mergeCards/API.py (класс Game), используется как есть.
Анимации строятся НЕ по эвристикам «до/после», а по ЖУРНАЛУ РЕАЛЬНЫХ событий
движка: mergeCards/trainer.py временно перехватывает _addrow/_merge/_compress
и записывает каждое каскадное смещение, слияние (vanish+pop) и доброс строк
ровно в том порядке, в котором их исполняет сам движок. Поэтому анимация
всегда совпадает с фактическим ходом игры клетка-в-клетку.

Координаты движка: y = 0 — ВЕРХНИЙ ряд; новые строки появляются сверху и
сдвигают карты вниз; карта, упавшая за нижний край (y >= size[1]) — смерть.
Перенос action_full((x, y), dst, count=n) берёт непрерывную серию из n карт,
начиная с (x, y) и ниже, и кладёт её ПОД нижнюю карту колонки-цели.

Управление:
    - клик по карте            — выбрать серию от этой карты до низа стопки;
    - ↑ / ↓, колесо мыши       — двигать ВЕРХНИЙ край серии;
    - Shift+↑ / Shift+↓        — двигать НИЖНИЙ край серии;
    - drag (зажать и тащить)  — перетащить серию в другую колонку;
    - клик по другой колонке   — перенести выбранную серию туда;
    - Shift+клик               — выбрать весь столбец;
    - ← / →                   — с клавиатуры выбор/перенос;
    - Space / «+ Строка»       — add_row;  Z — отменить ход;
    - R — новая игра;           Esc — закрыть окно.

Запуск:  python main.py   (из папки mergeCards)
         python mergeCards/main.py   (из корня репозитория)
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer        # noqa: E402
from PyQt5.QtGui import (                                    # noqa: E402
    QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPen,
)
from PyQt5.QtWidgets import (                                # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)

try:  # игровой API лежит рядом, в mergeCards/
    from API import Game, Card, pretty_number                # noqa: E402
    from trainer import EventRecorder                        # noqa: E402
except Exception as exc:  # pragma: no cover
    print("Не удалось импортировать mergeCards/API.py:", exc)
    raise

# ---------------------------------------------------------------------------
# Константы оформления
# ---------------------------------------------------------------------------

BOARD_COLS = 6
BOARD_ROWS = 7
CELL = 92               # размер клетки, px
GAP = 10                # зазор между картами
PAD = 16                # отступ поля от края

BG_TOP = QColor("#141a2b")
BG_BOTTOM = QColor("#0b0e18")

LEVEL_COLORS = [
    ("#3d4663", "#cfe0ff"),  # 0 — заглушка (пустая клетка)
    ("#4a6cf7", "#ffffff"),  # 1
    ("#2fbf8f", "#ffffff"),  # 2
    ("#f0a13a", "#1d2333"),  # 3
    ("#ef5da8", "#ffffff"),  # 4
    ("#8e5cf7", "#ffffff"),  # 5
    ("#38c1d4", "#10202c"),  # 6
    ("#f75f5f", "#ffffff"),  # 7
    ("#ffd23f", "#3c2f00"),  # 8
    ("#7de65c", "#15300c"),  # 9
    ("#b0b7c9", "#20242e"),  # 10
]


def level_color(value: int):
    """Цвет фона/текста карты по уровню (выше палитры — лёгкий дрейф тона)."""
    if value <= 0:
        return None, None
    idx = min(value, len(LEVEL_COLORS) - 1)
    base, text = LEVEL_COLORS[idx]
    if value > len(LEVEL_COLORS) - 1:
        c = QColor(base)
        shift = ((value * 37) % 60) - 30
        c.setHsv((c.hue() + shift) % 360, max(c.saturation() - 10, 40),
                 min(max(c.value(), 40), 255))
        return c.name(), text
    return base, text


def card_label(value: int) -> str:
    """Текст на карте: 2**value (через pretty_number API, крупные — 10^N)."""
    n = 1 << value
    s = str(pretty_number(n))
    if len(s) > 9:
        mant, _, exp = s.partition(" * 10^")
        if exp:
            return f"10^{int(exp)}"
    return s


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_inout(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Планировщик: журнал событий движка -> кадры анимации
# ---------------------------------------------------------------------------

class AnimScheduler:
    """Превращает ЖУРНАЛ событий движка в список кадров анимации.

    Каждый кадр — ПОЛНОЕ состояние доски: словарь статичных карт плюс список
    «движущихся» элементов. Карта физически не может исчезнуть и появиться
    из ниоткуда: она либо стоит, либо летит между клетками (fly/slide),
    либо гаснет/рождается (fade/pop/drop).

    Журнал пишется mergeCards/trainer.py, который перехватывает примитивы
    движка и записывает события строго в порядке их исполнения:
        transfer — сам перенос серии (движок делает его «молча»:
                   frm=[клетки-источники], v=[значения], dst, dest_h —
                   куда серия ляжет ДО слияний);
        slide    — каскад при добросе строк или compress после слияний;
        vanish+pop — пара схлопывающихся карт и рождение результата;
        spawn    — карты новой(ых) строки(ок) сверху.

    build() проигрывает журнал на копии поля и превращает его в сегменты
    ('fly', 'growth', 'merge'), затем сверяет результат с финальным снимком
    движка (reconcile): несошедшие сегменты отбрасываются, недостающие
    изменения достраиваются. Поэтому анимация всегда доводит поле «до»
    ровно до поля «после». frames() разворачивает сегменты в покадровый
    список для плеера BoardWidget.
    """

    FLY_MS      = 360.0   # полёт серии (все карты летят разом, со стагом)
    FLY_STAGGER = 55.0    # задержка между картами серии, мс
    VANISH_MS   = 170.0   # схлопывание пары
    POP_MS      = 230.0   # рождение результата слияния (со вспышкой)
    SETTLE_MS   = 150.0   # оседание колонки после слияния
    SLIDE_MS    = 240.0   # сдвиг каскада (строки едут одновременно)
    SPAWN_MS    = 300.0   # въезд строки сверху
    TICK        = 16      # мс на кадр

    # ---------- построение сегментов из журнала ----------------------------
    @staticmethod
    def _replay(events, before, cols, H):
        sim = {(x, y): v for (x, y), v in before.items() if v}
        segs = []
        pend_vanish = None
        open_growth = False

        def growth():
            nonlocal open_growth
            if not (open_growth and segs and segs[-1]['t'] == 'growth'):
                segs.append({'t': 'growth', 'items': []})
            open_growth = True
            return segs[-1]

        for ev in events:
            op = ev['op']

            if op == 'transfer':
                moved = []
                for i, (f, v) in enumerate(zip(ev['frm'], ev['v'])):
                    t = (ev['dst'], ev['dest_h'] + i)
                    moved.append((tuple(f), tuple(t), v))
                if moved:
                    # перенос записан ДО применения; сверяем с симуляцией:
                    # источник должен стоять на своих местах, а клетки
                    # назначения — быть свободны (движок кладёт серию встык)
                    h_src = next((y for y in range(H)
                                  if sim.get((moved[0][0][0], y))), None)
                    col0 = moved[0][0][0]
                    contig = sorted(y for (cx, y), vv in sim.items()
                                    if cx == col0 and vv)
                    series_ok = (contig[:len(moved)] ==
                                 [m[0][1] for m in moved])
                    free_ok = all(not sim.get(t) or sim.get(t) == v
                                  for _f, t, v in moved)
                    if (all(sim.get(f) == v for f, _t, v in moved)
                            and series_ok and free_ok):
                        for f, t, v in moved:
                            sim[f] = 0
                            sim[t] = v
                        segs.append({'t': 'fly', 'moves': moved})
                        open_growth = False

            elif op == 'slide':
                frm, to, v = tuple(ev['frm']), tuple(ev['to']), ev['v']
                if sim.get(frm) != v:
                    continue
                sim[frm] = 0
                sim[to] = v
                g = growth()
                g['items'].append(('slide', frm, to, v))
                # если slide уходит из клетки, куда серия села только что,
                # источник не надо вычитать из базового слоя — он и так пуст
                if segs and segs[-1]['t'] == 'fly' \
                        and any(t == frm for _f, t, _v in
                                segs[-1]['moves']):
                    g.setdefault('from_fly', set()).add(frm)

            elif op == 'spawn':
                at, v = tuple(ev['at']), ev['v']
                sim[at] = v
                g = growth()
                g['items'].append(('spawn', at, v))
                if segs and segs[-1]['t'] == 'fly' \
                        and any(t == at for _f, t, _v in
                                segs[-1]['moves']):
                    g.setdefault('from_fly', set()).add(at)

            elif op == 'vanish':
                pend_vanish = ev

            elif op == 'pop':
                if pend_vanish is None:
                    continue
                va, pa = tuple(pend_vanish['at']), tuple(ev['at'])
                v_old, v_new = pend_vanish['v'], ev['v']
                pend_vanish = None
                if sim.get(va) != v_old or sim.get(pa) != v_new:
                    continue
                # При слипании всё, что было НИЖЕ vanishing-клетки, едет
                # вверх ровно на одну клетку. Клетка pop'а (бывшая верхняя
                # пары) остаётся на месте и НЕ двигается. Движок после
                # каждого слияния делает полный каскад _compress — его
                # slide/spawn из журнала поглощаются этим же сегментом
                # ('absorb'), чтобы не превратиться в отдельный запоздалый
                # каскад.
                settle = []
                for y in range(va[1] + 1, H):
                    vv = sim.get((va[0], y))
                    if vv and y != pa[1]:
                        settle.append(((va[0], y), (va[0], y - 1), vv))
                sim[va] = 0
                sim[pa] = v_new
                for f, t, v2 in settle:
                    sim[f] = 0
                    sim[t] = v2
                open_growth = False
                segs.append({'t': 'merge', 'vanish': (va, v_old),
                             'pop': (pa, v_new), 'settle': settle,
                             'absorb': True})

        # после слияния движок трамбует колонку до конца (полный каскад
        # _compress) — эти slide/spawn относятся к тому же моменту времени
        for si, s in enumerate(segs):
            if s['t'] == 'merge' and s.get('absorb'):
                j = si + 1
                while j < len(segs) and segs[j]['t'] == 'growth':
                    s['settle'].extend(
                        it[1:] for it in segs[j]['items'] if it[0] == 'slide')
                    s['extra_spawn'] = [it[1:] for it in segs[j]['items']
                                        if it[0] == 'spawn']
                    j += 1
                del segs[si + 1:j]
        # упорядочиваем поглощённые сдвиги каскадом (низ -> вверх), чтобы
        # применение было последовательным: y+1 -> y раньше, чем y+2 -> y+1
        for s in segs:
            if s['t'] == 'merge' and s.get('absorb'):
                s['settle'].sort(key=lambda m: -m[0][1])
        return segs

    # ---------- длительности ----------------------------------------------
    @staticmethod
    def _dur(seg):
        S = AnimScheduler
        if seg['t'] == 'fly':
            return S.FLY_MS + min(len(seg['moves']) * S.FLY_STAGGER, 250.0)
        if seg['t'] == 'growth':
            slides = [it for it in seg['items'] if it[0] == 'slide']
            spawns = [it for it in seg['items'] if it[0] == 'spawn']
            rows = sorted({it[3] for it in slides} |
                          {it[1][1] for it in spawns})
            base = S.SLIDE_MS if slides else S.SPAWN_MS
            extra = max((S.SLIDE_MS * 0.35 * (r - rows[0])
                         for r in rows[1:]), default=0.0)
            return max(base + extra, S.SPAWN_MS)
        if seg['t'] == 'merge':
            depth = max([t[1] for _f, t, _v in seg['settle']] or [0])
            return (S.VANISH_MS + S.POP_MS
                    + (S.SETTLE_MS * (1.0 + 0.25 * depth)
                       if seg['settle'] else 0.0))
        return 200.0

    # ---------- применение сегмента к полю ----------------------------------
    @staticmethod
    def _apply_seg(field, seg):
        if seg['t'] == 'fly':
            for f, t, v in seg['moves']:
                field[f] = 0
                field[t] = v
        elif seg['t'] == 'growth':
            for it in seg['items']:
                if it[0] == 'slide':
                    _k, f, t, v = it
                    field[f] = 0
                    field[t] = v
                else:
                    _k, at, v = it
                    field[at] = v
        elif seg['t'] == 'merge':
            va, pa = seg['vanish'][0], seg['pop'][0]
            field[va] = 0
            field[pa] = seg['pop'][1]
            for f, t, v in seg['settle']:
                field[f] = 0
                field[t] = v
            for at, v in seg.get('extra_spawn', []):
                field[at] = v

    @staticmethod
    def _seg_ok(field, seg):
        """Сегмент применим к текущему состоянию истории?"""
        if seg['t'] == 'fly':
            return all(field.get(f) == v for f, _t, v in seg['moves']) \
                and all(not field.get(t) for _f, t, _v in seg['moves'])
        if seg['t'] == 'growth':
            for it in seg['items']:
                if it[0] == 'slide':
                    if field.get(it[1]) != it[3]:
                        return False
                else:
                    if field.get(it[1]):
                        return False
            return True
        if seg['t'] == 'merge':
            va, vold = seg['vanish']
            pa, vnew = seg['pop']
            if field.get(va) != vold or field.get(pa) != vnew:
                return False
            snap = dict(field)
            snap[va] = 0
            snap[pa] = vnew
            for f, t, v in seg['settle']:      # каскад применяется по порядку
                if snap.get(f) != v:
                    return False
                snap[f] = 0
                snap[t] = v
            for at, v in seg.get('extra_spawn', []):
                if snap.get(at):
                    return False
            return True
        return False

    # ---------- сборка фаз --------------------------------------------------
    @staticmethod
    def build(events, before, after, cols, rows, hidden_rows):
        S1 = rows
        vis_after = {k: v for k, v in after.items() if v and k[1] < S1}
        segs = AnimScheduler._replay(events, before, cols, hidden_rows)

        # a) оставляем только сходящиеся сегменты
        field = {k: v for k, v in before.items() if v}
        kept = []
        for s in segs:
            if not AnimScheduler._seg_ok(field, s):
                continue
            AnimScheduler._apply_seg(field, s)
            kept.append(s)

        # b) недостающие изменения достраиваем одним growth-сегментом
        missing = {k: v for k, v in vis_after.items() if field.get(k) != v}
        extra = {k: v for k, v in field.items()
                 if v and k[1] < S1 and vis_after.get(k) != v}
        items = []
        used = set()
        for (ax, ay), av in sorted(extra.items()):
            cand = [ty for (tx, ty) in list(missing)
                    if tx == ax and ty > ay and missing[(tx, ty)] == av
                    and (ax, ty) not in used]
            if cand:
                ty = min(cand)
                items.append(('slide', (ax, ay), (ax, ty), av))
                used.add((ax, ty))
                del missing[(ax, ty)]
                del extra[(ax, ay)]
                field[(ax, ay)] = 0
                field[(ax, ty)] = av
        for (mx, my), mv in sorted(missing.items()):
            items.append(('spawn', (mx, my), mv))
            field[(mx, my)] = mv
        for (ex, ey) in sorted(extra):
            field[(ex, ey)] = 0     # лишнее молча уходит (за край и т.п.)
        if items:
            kept.append({'t': 'growth', 'items': items})

        # c) финальная проверка согласованности всей истории
        field = {k: v for k, v in before.items() if v}
        for s in kept:
            AnimScheduler._apply_seg(field, s)
        bad = ({k: v for k, v in field.items() if v} != vis_after)
        if bad:                      # история не сходится — не анимируем
            return []
        for s in kept:
            s['dur'] = AnimScheduler._dur(s)
        return kept

    # ---------- кадры --------------------------------------------------------
    @staticmethod
    def frames(phases, before, after, rows):
        """Полный расчёт всех кадров заранее (детерминированный плеер)."""
        S1, TICK = rows, AnimScheduler.TICK
        S = AnimScheduler
        frames = []
        field = {k: v for k, v in before.items() if v}

        def snap(excl):
            return {k: v for k, v in field.items() if v and k[1] < S1
                    and k not in excl}

        for s in phases:
            dur = max(s['dur'], 2 * TICK)
            n = max(int(dur // TICK), 2)

            if s['t'] == 'fly':
                moves = s['moves']
                excl = {m[0] for m in moves} | {m[1] for m in moves}
                for i in range(n):
                    u = (i + 1) / n
                    anim, landed = [], {}
                    for j, (f, t, v) in enumerate(moves):
                        d = min(j * S.FLY_STAGGER / dur, 0.5)
                        uu = max(0.0, min(1.0, (u - d) / max(1 - d, 1e-9)))
                        if uu >= 1.0:
                            landed[t] = v          # села — рисуем статично
                        else:
                            anim.append(('fly', f[0], f[1], t[0], t[1], v,
                                         ease_inout(uu)))
                    base = snap(excl)
                    base.update(landed)
                    frames.append({'base': base, 'anim': anim})
                AnimScheduler._apply_seg(field, s)

            elif s['t'] == 'growth':
                slides = [(it[1], it[2], it[3]) for it in s['items']
                          if it[0] == 'slide']
                spawns = [(it[1], it[2]) for it in s['items']
                          if it[0] == 'spawn']
                from_fly = s.get('from_fly', set())
                excl = ({f for f, _t, _v in slides if f not in from_fly}
                        | {t for _f, t, _v in slides}
                        | {a for a, _v in spawns})
                for i in range(n):
                    u = (i + 1) / n
                    anim, landed = [], {}
                    for (fx, fy), (tx, ty), v in slides:
                        d = 0.35 * fy / max(S1 - 1, 1)
                        uu = max(0.0, min(1.0, (u - d) / max(1 - d, 1e-9)))
                        if uu >= 1.0:
                            landed[(tx, ty)] = v
                        else:
                            anim.append(('slide', fx, fy, tx, ty, v,
                                         ease_inout(uu)))
                    for at, v in spawns:
                        d = 0.30 * at[1] / max(S1 - 1, 1)
                        uu = max(0.0, min(1.0, (u - d) / max(1 - d, 1e-9)))
                        if uu >= 1.0:
                            landed[at] = v
                        else:
                            anim.append(('drop', at[0], at[1], v,
                                         ease_out(uu)))
                    base = snap(excl)
                    base.update(landed)
                    frames.append({'base': base, 'anim': anim})
                AnimScheduler._apply_seg(field, s)

            elif s['t'] == 'merge':
                (va, vold), (pa, vnew) = s['vanish'], s['pop']
                settle = s['settle']
                espawn = s.get('extra_spawn', [])
                v_end = S.VANISH_MS / dur
                p_end = (S.VANISH_MS + S.POP_MS) / dur
                excl = ({va, pa} | {f for f, _t, _v in settle}
                        | {t for _f, t, _v in settle}
                        | {a for a, _v in espawn})
                for i in range(n):
                    u = (i + 1) / n
                    anim, landed = [], {}
                    vu = min(1.0, u / max(v_end, 1e-9))
                    if vu < 1.0:
                        anim.append(('fade', va[0], va[1], vold,
                                     ease_inout(vu)))
                    pu = max(0.0, min(1.0, (u - v_end) /
                                      max(p_end - v_end, 1e-9)))
                    if pu < 1.0:
                        anim.append(('pop', pa[0], pa[1], vnew, pu))
                    else:
                        landed[pa] = vnew
                    su = max(0.0, min(1.0, (u - p_end) /
                                      max(1 - p_end, 1e-9)))
                    for (fx, fy), (tx, ty), v in settle:
                        if su >= 1.0:
                            landed[(tx, ty)] = v
                        else:
                            anim.append(('slide', fx, fy, tx, ty, v,
                                         ease_inout(su)))
                    for at, v in espawn:
                        if su >= 1.0:
                            landed[at] = v
                        else:
                            anim.append(('drop', at[0], at[1], v,
                                         ease_out(su)))
                    base = snap(excl)
                    base.update(landed)
                    frames.append({'base': base, 'anim': anim})
                AnimScheduler._apply_seg(field, s)

        frames.append({'base': {k: v for k, v in after.items()
                                if v and k[1] < S1}, 'anim': []})
        return frames


# ---------------------------------------------------------------------------
# Виджет игрового поля
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    """Рисует поле Game и обрабатывает клики/drag-and-drop."""

    TICK_MS = 16

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected = None        # (x, y_lo, y_hi) — выбранная серия
        self.hover_col = None
        # drag-and-drop
        self.dragging = False
        self.drag_pos = None
        self._press_cell = None
        self._press_pos = None
        self._press_moved = False
        self._drag_series = None

        # плеер анимаций: кадры рассчитываются ЗАРАНЕЕ по журналу движка,
        # проигрываются строго по порядку — поле в кадре всегда полное
        self.anim_active = False
        self._frames = []
        self._fi = 0
        self._pending_gameover = None

        self._anim = QTimer(self)
        self._anim.setInterval(self.TICK_MS)
        self._anim.timeout.connect(self._on_anim_tick)

        self.setMinimumSize(self.board_px_w(), self.board_px_h())
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # --- размеры ----------------------------------------------------------
    def board_px_w(self):
        return PAD * 2 + self.game.size[0] * CELL + (self.game.size[0] - 1) * GAP

    def board_px_h(self):
        return PAD * 2 + self.game.size[1] * CELL + (self.game.size[1] - 1) * GAP

    def col_rect(self, x):
        return QRectF(PAD + x * (CELL + GAP), PAD,
                      CELL, self.board_px_h() - 2 * PAD)

    def cell_center(self, x, y):
        """Центр клетки (x, y). y = 0 — верхний видимый ряд (как в движке)."""
        return QPointF(PAD + x * (CELL + GAP) + CELL / 2,
                       PAD + y * (CELL + GAP) + CELL / 2)

    def cell_rect(self, x, y):
        c = self.cell_center(x, y)
        return QRectF(c.x() - CELL / 2, c.y() - CELL / 2, CELL, CELL)

    def _col_at(self, pos: QPointF):
        for x in range(self.game.size[0]):
            if self.col_rect(x).contains(pos):
                return x
        return None

    # --- состояние поля ---------------------------------------------------
    def occupied(self, x):
        return [y for y in range(self.game.size[1]) if self.value_at(x, y)]

    def value_at(self, x, y):
        if 0 <= x < self.game.size[0] and 0 <= y < self.game.size_calc[1]:
            return self.game.field[x][y].value
        return 0

    def stack_height(self, x):
        return len(self.occupied(x))

    def series_count(self, sel=None):
        if sel is None:
            sel = self.selected
        if not sel:
            return 0
        return sel[2] - sel[1] + 1

    def clamp_sel(self, x, y):
        occ = self.occupied(x)
        if not occ:
            return None
        for yy in occ:
            if yy >= y:
                return yy
        return occ[-1]

    # --- клонирование / проверка ходов -------------------------------------
    def _clone(self) -> Game:
        win = self.window()
        if win is not None and hasattr(win, "clone_game"):
            return win.clone_game(self.game)
        return self._fallback_clone(self.game)

    @staticmethod
    def _fallback_clone(g: Game) -> Game:
        cards = {}
        for x in range(g.size[0]):
            for y in range(g.size_calc[1]):
                v = g.field[x][y].value
                if v:
                    cards.setdefault(x, {})[y] = Card((x, y), v)
        gc = Game(g.size, cards, points=g.points, bestCombo=g.bestCombo,
                  _bag=list(g._bag))
        gc.lastCombo = list(g.lastCombo)
        return gc

    def snapshot(self, g: Game):
        """{(x, y): value} всех занятых клеток (включая скрытые за краем)."""
        return {(x, y): g.field[x][y].value
                for x in range(g.size[0])
                for y in range(g.size_calc[1]) if g.field[x][y].value}

    def _engine_move(self, g: Game, sx, sy, dx, cnt):
        """Ход НАД ЗАХВАТЧИКОМ: возвращает (result, added, events)."""
        with EventRecorder(g) as rec:
            try:
                res, added = g.action_full((sx, sy), dx, count=cnt)
            except Exception:
                return "Invalid position", False, []
        if isinstance(res, tuple):
            res = str(res[0])
        return res, bool(added), list(rec.events)

    def _try_move_probe(self, sx, sy, dx, cnt):
        """Прогон хода на независимой копии. None — ход невозможен."""
        gc = self._clone()
        res, added, evs = self._engine_move(gc, sx, sy, dx, cnt)
        if res == "Invalid position":
            return None
        return res, gc, added, evs

    def legal_moves(self):
        """Все принятые движком переносы серий. (sx, sy, dx, cnt, result)."""
        out = []
        seen = set()
        for sx in range(self.game.size[0]):
            occ = self.occupied(sx)
            if not occ:
                continue
            for a in range(len(occ)):
                for b in range(a, len(occ)):
                    if occ[b] != occ[a] + (b - a):
                        break
                    sy, cnt = occ[a], b - a + 1
                    for dx in range(self.game.size[0]):
                        if dx == sx or (sx, sy, dx, cnt) in seen:
                            continue
                        seen.add((sx, sy, dx, cnt))
                        r = self._try_move_probe(sx, sy, dx, cnt)
                        if r is None:
                            continue
                        out.append((sx, sy, dx, cnt, r[0]))
        return out

    def add_row_survives(self):
        try:
            gc = self._clone()
            with EventRecorder(gc):
                res = gc.add_row()
        except Exception:
            return False
        if isinstance(res, tuple):
            return False
        if res is not None and str(res).startswith("Game Over"):
            return False
        return not any(gc.field[x][gc.size[1]].value
                       for x in range(gc.size[0]))

    def has_any_action(self):
        win = self.window()
        if win is not None and getattr(win, "game_over", False):
            return False
        if self.legal_moves():
            return True
        return self.add_row_survives()

    # --- выполнение хода ---------------------------------------------------
    def try_move(self, src_x, src_y, dst_x, count_rows=None):
        """Перенос серии; возвращает True, если ход был совершён.

        Ход исполняется ОДИН раз — на оригинале, но под EventRecorder:
        журнал событий движка и есть сценарий анимации, поэтому экран не
        может разъехаться с полем (никаких «пробных прогонов на копии»,
        которые раньше давали два разных финала из-за randint в движке).
        """
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return False
        if count_rows is None:
            count_rows = 1
        if src_x == dst_x or count_rows <= 0:
            self.selected = None
            self.update()
            return False
        if not any(self.value_at(src_x, y)
                   for y in range(src_y, min(src_y + count_rows,
                                             self.game.size[1]))):
            self.selected = None
            win.show_status("Источник пуст — нечего переносить.", warn=True)
            self.update()
            return False

        before = self.snapshot(self.game)
        win.push_undo()
        res, _added, evs = self._engine_move(self.game, src_x, src_y,
                                             dst_x, count_rows)
        after = self.snapshot(self.game)
        self.selected = None

        msg = None
        if res != "Success":
            msg = str(res)          # «Game Over...» или «Invalid position»
            win.show_status(f"Ход отвергнут движком: {msg}", warn=True)
        else:
            combo = max(self.game.lastCombo) if self.game.lastCombo else 0
            win.show_status(f"Комбо x{combo}!" if combo > 1
                            else f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        self._play(before, after, evs, gameover_msg=msg)
        if msg is None:
            win.check_deadlock()
        return True

    def do_add_row(self):
        """«+ Строка»: тот же принцип — один честный прогон с журналом."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return
        before = self.snapshot(self.game)
        win.push_undo()
        try:
            with EventRecorder(self.game) as rec:
                res = self.game.add_row()
            evs = list(rec.events)
        except Exception:
            res = "Game Over: engine exception"
            evs = []
        after = self.snapshot(self.game)
        self.selected = None

        bad = (isinstance(res, tuple) or
               (res is not None and str(res).startswith("Game Over")))
        msg = None
        if bad:
            msg = str(res)
        else:
            win.show_status("Добавлена новая строка сверху.")
        win.refresh_stats()
        self._play(before, after, evs, gameover_msg=msg)
        if not bad:
            win.check_deadlock()

    # --- плеер анимаций -----------------------------------------------------
    def _play(self, before, after, events, gameover_msg=None):
        """Кадры считаются ЗАРАНЕЕ по журналу движка и сверяются с before/
        after. Если история не сходится клетка-в-клетку — показываем финал
        мгновенно (честнее, чем рисовать неверную анимацию)."""
        phases = AnimScheduler.build(events, before, after,
                                     self.game.size[0], self.game.size[1],
                                     self.game.size_calc[1])
        frames = AnimScheduler.frames(phases, before, after,
                                      self.game.size[1])
        self._frames = frames
        self._fi = 0
        self._pending_gameover = gameover_msg
        # меньше двух кадров анимировать нечего
        if len(self._frames) < 2:
            self._finish_anim()
            return
        self.anim_active = True
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    def _on_anim_tick(self):
        if not self.anim_active:
            return
        self._fi += 1
        if self._fi >= len(self._frames):
            self._finish_anim()
            return
        self.update()

    def _finish_anim(self):
        self.anim_active = False
        self._frames = []
        self._fi = 0
        self._anim.stop()
        win = self.window()
        if self._pending_gameover is not None and win is not None:
            win.on_game_over(self._pending_gameover)
        self._pending_gameover = None
        self.update()

    # --- события мыши -------------------------------------------------------
    @staticmethod
    def _ev_pos_f(ev) -> QPointF:
        try:
            return ev.position()
        except AttributeError:
            pass
        try:
            return ev.localPos()
        except AttributeError:
            return QPointF(ev.pos())

    def _cell_at(self, pos):
        x = self._col_at(QPointF(pos.x(), PAD + 1))
        if x is None:
            return None, None
        row = int((pos.y() - PAD) // (CELL + GAP))
        if 0 <= row <= self.game.size[1] - 1:
            return x, row
        return None, None

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton or self.anim_active:
            return
        pos = self._ev_pos_f(ev)
        x, y = self._cell_at(pos)
        self._press_cell = (x, y)
        self._press_pos = QPointF(pos)
        self._press_moved = False
        self._drag_series = None
        if x is None:
            self.selected = None
            self.update()
            return
        occ = self.occupied(x)
        prev = self.selected
        if not occ:
            if prev is not None:
                self.try_move(prev[0], prev[1], x, self.series_count(prev))
            else:
                self.selected = None
                self.update()
            return
        hit = y if self.value_at(x, y) else self.clamp_sel(x, y)
        if (ev.modifiers() & Qt.ShiftModifier
                and not (prev and prev[0] != x)):
            self.selected = (x, occ[0], occ[-1])
            self._announce_selection()
            self.update()
            return
        if prev is not None and prev[0] != x:
            self.try_move(prev[0], prev[1], x, self.series_count(prev))
            return
        if prev is not None and prev[0] == x:
            if prev[1] < hit <= prev[2] or (hit == prev[1] and
                                            prev[1] == prev[2]):
                if prev[1] == hit == prev[2]:
                    self.selected = None
                else:
                    self._drag_series = (prev[1], prev[2])
                self.update()
                return
            if hit == prev[1]:
                self.selected = None
                self.update()
                return
        self.selected = (x, hit, occ[-1])
        self._drag_series = (hit, occ[-1])
        self._announce_selection()
        self.update()

    def _announce_selection(self):
        win = self.window()
        if win is None or self.selected is None or win.is_game_over():
            return
        x, lo, hi = self.selected
        occ = self.occupied(x)
        cnt = hi - lo + 1
        if lo == occ[0] and hi == occ[-1]:
            win.show_status(f"Выбрана колонка {x + 1} целиком ({cnt} карт). "
                            "Тащите мышью или кликните по другой колонке.")
        else:
            win.show_status(f"Выбрана серия из {cnt} карт (колонка {x + 1}, "
                            f"ряды {lo + 1}–{hi + 1}). ↑/↓ — верхний край, "
                            "Shift+↑/↓ — нижний, drag — перетащить.")

    def mouseMoveEvent(self, ev):
        pos = self._ev_pos_f(ev)
        self.hover_col = self._col_at(QPointF(pos.x(), PAD + 1))
        if (self._press_cell is not None and (ev.buttons() & Qt.LeftButton)
                and not self.anim_active):
            px, py = self._press_cell
            if px is not None:
                if (abs(pos.x() - self._press_pos.x()) > 6 or
                        abs(pos.y() - self._press_pos.y()) > 6):
                    if self.selected and self.selected[0] == px:
                        self._drag_series = (self.selected[1],
                                             self.selected[2])
                    else:
                        occ = self.occupied(px)
                        if occ:
                            hit = py if self.value_at(px, py) else \
                                self.clamp_sel(px, py)
                            self.selected = (px, hit, occ[-1])
                            self._drag_series = (hit, occ[-1])
                    if self._drag_series and self.selected \
                            and self.selected[0] == px:
                        self._press_moved = True
                        self.dragging = True
                        self.drag_pos = QPointF(pos)
        elif not (ev.buttons() & Qt.LeftButton):
            self.dragging = False
            self.drag_pos = None
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        was_drag = self.dragging and self._press_moved
        drop_pos = self._ev_pos_f(ev)
        press_x = self._press_cell[0] if self._press_cell else None
        self.dragging = False
        self.drag_pos = None
        self._press_cell = None
        self._press_pos = None
        self._press_moved = False
        if self.anim_active:
            self.update()
            return
        if not was_drag:
            self.update()
            return
        sx = press_x
        if sx is None or self._drag_series is None:
            self.update()
            return
        tx = self._col_at(QPointF(drop_pos.x(), PAD + 1))
        if tx is None or tx == sx:
            self.update()
            return
        lo, hi = self._drag_series
        self.try_move(sx, lo, tx, hi - lo + 1)

    def wheelEvent(self, ev):
        """Колесо — верхний край серии; Shift+колесо — нижний."""
        if self.anim_active or self.selected is None:
            return
        x, lo, hi = self.selected
        occ = self.occupied(x)
        if not occ:
            self.selected = None
            self.update()
            return
        dy = -1 if ev.angleDelta().y() > 0 else 1
        if ev.modifiers() & Qt.ShiftModifier:
            nhi = max(lo, min(occ[-1], hi + dy))
            if nhi != hi:
                self.selected = (x, lo, nhi)
                self._announce_selection()
        else:
            nlo = max(occ[0], min(hi, lo + dy))
            if nlo != lo:
                self.selected = (x, nlo, max(hi, nlo))
                self._announce_selection()
        self.update()

    def leaveEvent(self, ev):
        self.hover_col = None
        self.dragging = False
        self.drag_pos = None
        self.update()

    def keyPressEvent(self, ev):
        key = ev.key()
        if self.anim_active:
            return
        mods = ev.modifiers()
        if key in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            x, lo, hi = self.selected
            occ = self.occupied(x)
            if not occ:
                self.selected = None
                self.update()
                return
            dy = -1 if key == Qt.Key_Up else 1
            if mods & Qt.ShiftModifier:
                nhi = max(lo, min(occ[-1], hi + dy))
                self.selected = (x, lo, nhi)
            else:
                nlo = max(occ[0], min(hi, lo + dy))
                self.selected = (x, nlo, max(hi, nlo))
            self._announce_selection()
            self.update()
            return
        if key in (Qt.Key_Left, Qt.Key_Right):
            dx = -1 if key == Qt.Key_Left else 1
            cols = self.game.size[0]
            if self.selected is None:
                cur = self.hover_col if self.hover_col is not None else 0
                for _ in range(cols):
                    cur = (cur + dx) % cols
                    if self.occupied(cur):
                        break
                if self.occupied(cur):
                    occ = self.occupied(cur)
                    self.selected = (cur, occ[0], occ[-1])
                    self._announce_selection()
            else:
                sx, lo, hi = self.selected
                self.try_move(sx, lo, (sx + dx) % cols, hi - lo + 1)
            self.update()
        elif key == Qt.Key_Escape:
            self.selected = None
            self.update()

    # --- отрисовка -----------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_frame(p)
        p.end()

    def _paint_frame(self, p):
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, BG_TOP)
        grad.setColorAt(1, BG_BOTTOM)
        p.fillRect(self.rect(), grad)

        field_rect = QRectF(PAD - 6, PAD - 6,
                            self.board_px_w() - 2 * PAD + 12,
                            self.board_px_h() - 2 * PAD + 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(field_rect, 16, 16)

        sel = self.selected
        sel_x = sel[0] if sel else None
        sel_lo = sel[1] if sel else None
        sel_hi = sel[2] if sel else None

        for x in range(self.game.size[0]):
            r = self.col_rect(x).adjusted(2, 2, -2, -2)
            if x == sel_x:
                p.setBrush(QColor(255, 210, 63, 36))
                p.drawRoundedRect(r, 14, 14)
                pen = QPen(QColor(255, 210, 63, 200))
                pen.setWidth(2)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r, 14, 14)
                p.setPen(Qt.NoPen)
            elif x == self.hover_col:
                p.setBrush(QColor(255, 255, 255, 18))
                p.drawRoundedRect(r, 14, 14)

        bottom_row = self.game.size[1] - 1
        danger = any(self.game.field[x][bottom_row].value
                     for x in range(self.game.size[0]))

        # ---- базовые карты ----
        if self.anim_active:
            self._paint_animated(p)
        else:
            hidden = set()
            dragging_now = (self.dragging and self.drag_pos is not None
                            and self._drag_series is not None
                            and self.selected is not None)
            if dragging_now and self.selected[0] == sel_x:
                for y in range(sel_lo, sel_hi + 1):
                    hidden.add((sel_x, y))
            for x in range(self.game.size[0]):
                occ = self.occupied(x)
                if not occ:
                    continue
                n = len(occ)
                for i, y in enumerate(occ):
                    if (x, y) in hidden:
                        continue
                    v = self.game.field[x][y].value
                    rect = self.cell_rect(x, y)
                    in_sel = (sel and x == sel_x and sel_lo <= y <= sel_hi)
                    scale = 1.0 + 0.03 * (i + 1) / max(n, 1) if in_sel else 1.0
                    dimmed = bool(sel) and not in_sel
                    self._draw_card(p, rect, v, scale=scale, dim=dimmed,
                                    ring=in_sel and y == sel_lo)

        if danger:
            pen = QPen(QColor(239, 93, 93, 220))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            line_y = PAD + self.game.size[1] * (CELL + GAP) - GAP + 4
            p.drawLine(int(PAD), int(line_y),
                       int(self.board_px_w() - PAD), int(line_y))

        # призрак перетаскиваемой серии (реальный drag-and-drop)
        if (self.dragging and self.drag_pos is not None
                and self._drag_series is not None and self.selected):
            gx, gy = self.drag_pos.x(), self.drag_pos.y()
            sx = self.selected[0]
            lo, hi = self._drag_series
            grab_row = lo
            if self._press_cell and self._press_cell[1] is not None \
                    and self.value_at(sx, self._press_cell[1]):
                grab_row = min(max(self._press_cell[1], lo), hi)
            dy0 = gy - self.cell_center(sx, grab_row).y()
            for y in range(lo, hi + 1):
                v = self.value_at(sx, y)
                if not v:
                    continue
                cy = self.cell_center(sx, y).y() + dy0
                rect = QRectF(gx - CELL / 2, cy - CELL / 2, CELL, CELL)
                self._draw_card(p, rect, v, alpha=210)
            tx = self._col_at(QPointF(gx, PAD + 1))
            if tx is not None and tx != sx:
                r = self.col_rect(tx).adjusted(2, 2, -2, -2)
                p.setPen(QPen(QColor(125, 230, 92, 220), 2))
                p.setBrush(QColor(125, 230, 92, 26))
                p.drawRoundedRect(r, 14, 14)
                p.setPen(Qt.NoPen)

    def _paint_animated(self, p: QPainter):
        """Рисует заранее рассчитанный кадр: статичные карты + движение.

        Кадры отдаёт AnimScheduler.frames(): 'base' — ВСЕ неподвижные карты
        доски на этот момент, 'anim' — летящие/едущие/гаснущие/рождающиеся
        карты. Ни одна карта не может пропасть: она либо в base, либо в anim.
        """
        if not (0 <= self._fi < len(self._frames)):
            return
        fr = self._frames[self._fi]
        for (x, y), v in fr['base'].items():
            self._draw_card(p, self.cell_rect(x, y), v)

        for item in fr['anim']:
            kind = item[0]
            if kind in ('fly', 'slide'):
                _k, fx, fy, tx, ty, v, u = item
                e = ease_inout(u)
                c1, c2 = self.cell_center(fx, fy), self.cell_center(tx, ty)
                if kind == 'fly':
                    arc = -min(abs(c2.x() - c1.x()), 140.0) * 0.35 \
                        * (4.0 * e * (1.0 - e))
                    cx = c1.x() + (c2.x() - c1.x()) * e
                    cy = c1.y() + (c2.y() - c1.y()) * e + arc
                    self._draw_card(p, QRectF(cx - CELL / 2, cy - CELL / 2,
                                              CELL, CELL), v,
                                    scale=1.0 + 0.06 * (1.0 - abs(0.5 - e)
                                                       * 2.0))
                else:
                    cy = c1.y() + (c2.y() - c1.y()) * e
                    rect = QRectF(c1.x() - CELL / 2, cy - CELL / 2,
                                  CELL, CELL)
                    if rect.bottom() > PAD and \
                            rect.top() < self.board_px_h() - PAD:
                        self._draw_card(p, rect, v)
            elif kind == 'fade':
                _k, x, y, v, u = item
                e = ease_inout(u)
                sc = 1.0 - 0.92 * e
                al = int(255 * (1.0 - e))
                if sc > 0.05 and al > 0:
                    self._draw_card(p, self.cell_rect(x, y), v,
                                    scale=sc, alpha=al)
            elif kind == 'pop':
                _k, x, y, v, u = item
                e = ease_out(u)
                sc = 0.35 + 0.65 * e
                if u < 0.55:
                    sc *= 1.0 + 0.18 * (u / 0.55)
                self._draw_card(p, self.cell_rect(x, y), v, scale=sc,
                                flash=int(350 * (1.0 - e)))
            elif kind == 'drop':
                _k, x, y, v, u = item
                e = ease_out(u)
                cy_target = PAD + y * (CELL + GAP) + CELL / 2
                start_y = -CELL / 2
                cy = start_y + (cy_target - start_y) * e
                rect = QRectF(PAD + x * (CELL + GAP), cy - CELL / 2,
                              CELL, CELL)
                self._draw_card(p, rect, v)

    def _draw_card(self, p: QPainter, rect: QRectF, value: int,
                   scale: float = 1.0, flash: int = 0, dim: bool = False,
                   ring: bool = False, alpha: int = 255):
        base, text = level_color(value)
        if base is None:
            return
        r = QRectF(rect)
        if scale != 1.0:
            cx, cy = r.center().x(), r.center().y()
            r.moveCenter(QPointF(0, 0))
            r.setWidth(r.width() * scale)
            r.setHeight(r.height() * scale)
            r.moveCenter(QPointF(cx, cy))

        c1 = QColor(base)
        if dim:
            c1 = c1.darker(170)
        c2 = c1.darker(135)
        lg = QLinearGradient(r.topLeft(), r.bottomRight())
        a0 = int(alpha * 0.9)
        lg.setColorAt(0, self._with_alpha(c1.lighter(112), a0))
        lg.setColorAt(1, self._with_alpha(c2, a0))

        p.setPen(Qt.NoPen)
        shadow = QRectF(r).translated(0, 3)
        p.setBrush(self._with_alpha(QColor(0, 0, 0), int(70 * alpha / 255)))
        p.drawRoundedRect(shadow, 14, 14)

        p.setBrush(lg)
        p.drawRoundedRect(r, 14, 14)

        path = QPainterPath()
        path.addRoundedRect(QRectF(r.x(), r.y(), r.width(), r.height() * 0.45),
                           14, 14)
        gloss = QLinearGradient(r.topLeft(), QPointF(r.left(), r.center().y()))
        gloss.setColorAt(0, QColor(255, 255, 255, int(55 * alpha / 255)))
        gloss.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, gloss)

        if flash:
            a = int(140 * (flash / 350.0))
            p.setBrush(QColor(255, 255, 255, a))
            p.drawRoundedRect(r, 14, 14)

        label = card_label(value)
        f = QFont("Segoe UI", 16, QFont.Bold)
        if len(label) > 5:
            f.setPointSize(11)
        if len(label) > 8:
            f.setPointSize(9)
        p.setFont(f)
        tc = QColor(text)
        tc.setAlpha(alpha)
        p.setPen(tc)
        p.drawText(r, Qt.AlignCenter, label)

        f2 = QFont("Consolas", 8)
        p.setFont(f2)
        lc = QColor(text).lighter(140) if QColor(text).value() < 160 \
            else QColor(text).darker(140)
        lc.setAlpha(alpha)
        p.setPen(lc)
        p.drawText(r.adjusted(6, 2, -6, -2), Qt.AlignTop | Qt.AlignLeft,
                   f"L{value}")

        if ring:
            pen = QPen(QColor("#ffd23f"), 3)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 14, 14)
            p.setPen(Qt.NoPen)

    @staticmethod
    def _with_alpha(color: QColor, alpha: int) -> QColor:
        c = QColor(color)
        c.setAlpha(max(0, min(255, alpha)))
        return c


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Merge Cards — PyQt5")
        self.setStyleSheet("QMainWindow { background: #0b0e18; }")
        self.game_over = False
        self._undo_stack = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("MERGE CARDS")
        title.setStyleSheet(
            "color:#eaf0ff; font: bold 22px 'Segoe UI'; letter-spacing:2px;")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.lbl_points = self._stat_box(stats, "Очки", "#4a6cf7")
        self.lbl_best = self._stat_box(stats, "Лучшее комбо", "#2fbf8f")
        self.lbl_max = self._stat_box(stats, "Макс. карта", "#f0a13a")
        root.addLayout(stats)

        self.game = self._fresh_game()
        self.board = BoardWidget(self.game)
        root.addWidget(self.board, 0, Qt.AlignHCenter)

        self.lbl_status = QLabel()
        self.lbl_status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_status)

        btns = QHBoxLayout()
        btns.addStretch(1)
        b_add = QPushButton("+ Строка")
        b_undo = QPushButton("Отменить (Z)")
        b_new = QPushButton("Новая игра")
        for b in (b_add, b_undo, b_new):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(130)
            b.setStyleSheet(
                "QPushButton { background:#232c47; color:#dfe7fa;"
                " border:1px solid #34406a; border-radius:10px;"
                " padding:8px 14px; font:600 13px 'Segoe UI'; }"
                "QPushButton:hover { background:#2d3860; }"
                "QPushButton:pressed { background:#1b2338; }")
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)
        b_add.clicked.connect(self.add_row_clicked)
        b_undo.clicked.connect(self.undo_last)
        b_new.clicked.connect(self.new_game)

        self.refresh_stats()
        self.show_status("Клик по карте — выбрать серию (она и всё под ней). "
                         "Drag — тащить серию мышью. ↑↓ — край серии, "
                         "Shift+↑↓ — нижний край. Space — строка, Z — отмена.")

    def _stat_box(self, layout: QHBoxLayout, name: str, accent: str) -> QLabel:
        box = QFrame()
        box.setStyleSheet(
            f"QFrame {{ background:#161d31; border:1px solid #26304e;"
            f" border-radius:12px; }} QLabel {{ color:#8f9cbf; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(2)
        cap = QLabel(name.upper())
        cap.setStyleSheet("color:#77839f; font: bold 10px 'Segoe UI';"
                          " letter-spacing:1px;")
        val = QLabel("0")
        val.setStyleSheet(f"color:{accent}; font: bold 20px 'Segoe UI';")
        lay.addWidget(cap)
        lay.addWidget(val)
        layout.addWidget(box)
        return val

    # ------- игра / undo -------
    def _fresh_game(self) -> Game:
        g = Game((BOARD_COLS, BOARD_ROWS), {})
        g.add_row()
        return g

    def _serialize(self, g: Game):
        return dict(
            field=[[g.field[x][y].value for y in range(g.size_calc[1])]
                   for x in range(g.size[0])],
            points=g.points, bestCombo=g.bestCombo, bag=list(g._bag),
            lastCombo=list(g.lastCombo),
        )

    def _deserialize(self, st) -> Game:
        cards = {}
        for x, col in enumerate(st["field"]):
            for y, v in enumerate(col):
                if v:
                    cards.setdefault(x, {})[y] = Card((x, y), v)
        g = Game((BOARD_COLS, BOARD_ROWS), cards)
        g.points = st["points"]
        g.bestCombo = st["bestCombo"]
        g._bag = list(st["bag"])
        g.lastCombo = list(st["lastCombo"])
        return g

    def clone_game(self, g: Game) -> Game:
        """Независимый клон (Game.copy() делит _bag по ссылке — баг API)."""
        return self._deserialize(self._serialize(g))

    def push_undo(self):
        self._undo_stack.append(self._serialize(self.game))
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def undo_last(self, reason: str = ""):
        if not self._undo_stack:
            if reason:
                self.show_status(reason, warn=True)
            return
        if self.board.anim_active:
            QTimer.singleShot(60, lambda: self.undo_last(reason))
            return
        st = self._undo_stack.pop()
        self.game = self._deserialize(st)
        self.board.game = self.game
        self.board.selected = None
        self.game_over = False
        self.refresh_stats()
        self.show_status("Ход отменён." if not reason else reason)
        self.board.update()

    # ------- логика UI -------
    def show_status(self, text: str, warn: bool = False):
        color = "#f75f5f" if warn else "#9aa7c7"
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            f"color:{color}; font:13px 'Segoe UI'; padding:4px;")

    def refresh_stats(self):
        self.lbl_points.setText(pretty_number(self.game.points, exact=True))
        self.lbl_best.setText(f"x{self.game.bestCombo}")
        mx = 0
        for x in range(self.game.size[0]):
            for y in range(self.game.size_calc[1]):
                mx = max(mx, self.game.field[x][y].value)
        self.lbl_max.setText(f"L{mx}")

    def add_row_clicked(self):
        if self.game_over or self.board.anim_active:
            return
        self.board.do_add_row()

    def board_game_changed(self, game: Game):
        self.game = game
        self.board.game = game

    def on_game_over(self, reason: str):
        self.game_over = True
        self.show_status(f"Игра окончена ({reason}). «Отменить» (Z) вернёт "
                         "последний ход, «Новая игра» или R — начнёт заново.",
                         warn=True)

    def is_game_over(self):
        return self.game_over

    def check_deadlock(self):
        if self.game_over:
            return
        if not self.board.has_any_action():
            self.on_game_over("не осталось ни ходов, ни места для новой "
                              "строки")

    def new_game(self):
        if self.board.anim_active:
            QTimer.singleShot(60, self.new_game)
            return
        self.game = self._fresh_game()
        self.board.game = self.game
        self.board.selected = None
        self.board.anim_active = False
        self.board._frames = []
        self.board._fi = 0
        self.board._anim.stop()
        self.board.setMinimumSize(self.board.board_px_w(),
                                  self.board.board_px_h())
        self._undo_stack.clear()
        self.game_over = False
        self.refresh_stats()
        self.show_status("Новая игра началась!")
        self.board.update()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self.add_row_clicked()
        elif ev.key() == Qt.Key_R:
            self.new_game()
        elif ev.key() == Qt.Key_Z:
            self.undo_last()
        elif ev.key() == Qt.Key_Escape:
            self.close()
        else:
            self.board.keyPressEvent(ev)


# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MergeCards")
    win = MainWindow()
    win.resize(win.sizeHint().expandedTo(
        win.centralWidget().layout().sizeHint()))
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
