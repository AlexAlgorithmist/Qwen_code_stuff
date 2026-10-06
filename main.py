--- main.py (原始)
# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) для игры "mergeCards" на PyQt5.

Игровая логика — mergeCards/API.py (класс Game), используется как есть.

Как устроен движок (важно для правил и анимаций):
    - y = 0 — ВЕРХНИЙ ряд поля; новые строки появляются сверху и сдвигают
      все карты вниз; карта, упавшая за нижний край (y >= size[1]) — смерть;
    - перенос action_full((x, y), dst, count=n) забирает НЕПРЕРЫВНУЮ серию
      из n карт, начиная с (x, y) и ниже, и кладёт её ПОД нижнюю карту
      колонки-цели; после этого колонка-цель сливается/схлопывается;
    - если слияний не было — движок добавляет ровно n строк сверху; если
      после хода на поле нет ни одной пары — добавляет строки дальше, пока
      поле не умрёт. «Бессмысленные» ходы реально приводят к Game Over.

Управление (перенести можно ЛЮБУЮ серию карт в колонке, а не только весь
столбец или хвост):
    - клик по карте              — выбрать серию, начинающуюся с этой карты;
    - ↑ / ↓ или колесо мыши      — двигать ВЕРХНИЙ край серии;
    - Shift+↑ / Shift+↓           — двигать НИЖНИЙ край серии;
    - drag (зажать и тащить)     — перетащить выбранную серию (или серию,
                                   захваченную прямо за любую её карту) в
                                   другую колонку одним жестом;
    - клик по другой колонке     — перенести выбранную серию туда;
    - Shift+клик                 — выбрать ВЕСЬ столбец;
    - ← / →                      — с клавиатуры: перенести серию в соседнюю
                                   колонку (если выбор есть), иначе выбрать
                                   ближайшую непустую колонку;
    - Space / «+ Строка»         — add_row;   Z — отменить последний ход;
    - R — новая игра;             Esc — закрыть окно.

Анимации воспроизводят механику движка покадрово (60 fps), а не просто
«исчезло/появилось»:
    * полёт переносимых карт (по дуге, со стагом), оседание колонок после
      слияний, схлопывание пар и pop результата слияния со вспышкой;
    * ВСЕ добавленные движком строки въезжают каскадом сверху, старые карты
      плавно съезжают вниз; несколько строк за один ход анимируются все;
    * во время перетаскивания серия следует за курсором (призрак), цель
      подсвечивается; после броска тот же самый ход проигрывается полной
      анимацией.
Движок при ходах без слияний использует randint — чтобы показанная анимация
совпадала с реальным финальным состоянием, ход сначала прогоняется на
независимой копии (у Game.copy() баг: _bag делится по ссылке), и если
движок добавил случайную строку, финалом становится состояние копии.

Честный проигрыш: предсказанный копией «Game Over...» применяется сразу
(без подтверждений); Game Over также наступает, когда не осталось ни одного
принятого движком хода и «+ Строка» убивает поле.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "mergeCards"))

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer        # noqa: E402
from PyQt5.QtGui import (                                    # noqa: E402
    QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPen,
)
from PyQt5.QtWidgets import (                                # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)

try:  # игровой API лежит в mergeCards/
    from API import Game, Card, pretty_number                # noqa: E402
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


def col_of(snap, x):
    """{y: value} занятых клеток колонки x по снимку {(x,y): v}."""
    return {y: v for (cx, y), v in snap.items() if cx == x and v}


def clean(col):
    return {y: v for y, v in col.items() if v}


class PlanningError(Exception):
    pass


# ---------------------------------------------------------------------------
# Планировщик анимаций
# ---------------------------------------------------------------------------

class MovePlanner:
    """Превращает переход «до/после» в протокол шагов анимации, честно
    воспроизводя механику движка.

    Шаги: fly(frm,to,v) — полёт карты; vanish(at,v) — карта исчезла
    (слияние); merge(at,v) — рождение результата слияния; slide(frm,to,v) —
    карта переехала (compress после слияний или cascade при появлении новых
    строк); spawn(at,v) — карта появилась из новой строки.

    Для переноса и для add_row используется ТОЧНАЯ симуляция: она обязана
    сойтись клетка-в-клетку с after, иначе это PlanningError (никаких
    эвристик-заглушек — именно они раньше давали эффект «анимаций нет»).
    """

    @staticmethod
    def plan(before, after, src_x=None, dst_x=None, series=None):
        if src_x is not None and dst_x is not None:
            steps = MovePlanner.simulate_move(before, after, src_x, dst_x,
                                              series)
            if steps is None:
                before = MovePlanner.reconcile(before, after, src_x, dst_x,
                                               series)
                steps = MovePlanner.simulate_move(before, after, src_x,
                                                  dst_x, series)
        else:
            steps = MovePlanner.simulate_addrow(before, after)
            if steps is None:
                before = MovePlanner.reconcile_addrow(before, after)
                steps = MovePlanner.simulate_addrow(before, after)
        if steps is None:
            raise PlanningError("переход не воспроизведён даже после "
                                "согласования снимков")
        return steps

    # --- фазы движения ----------------------------------------------------
    @staticmethod
    def phases(steps):
        """Протокол шагов -> список фаз [(kind, dur_ms, [events]), ...]."""
        out = []
        for st in steps:
            kind = st['op']
            if kind == 'fly':
                ev = {'kind': 'fly', 'frm': st['frm'], 'to': st['to'],
                      'v': st['v']}
            elif kind == 'vanish':
                ev = {'kind': 'vanish', 'at': st['at'], 'v': st['v']}
            elif kind == 'merge':
                ev = {'kind': 'pop', 'at': st['at'], 'v': st['v']}
            elif kind == 'slide':
                ev = {'kind': 'slide', 'frm': st['frm'], 'to': st['to'],
                      'v': st['v']}
            elif kind == 'spawn':
                ev = {'kind': 'spawn', 'at': st['at'], 'v': st['v']}
            else:
                continue
            if out and out[-1][0] == kind:
                out[-1][2].append(ev)
            else:
                out.append([kind, 0.0, [ev]])
        res = []
        for kind, _, evs in out:
            if kind == 'fly':
                evs.sort(key=lambda e: e['to'][1])
                dur = BoardWidget.FLY_MS + min(len(evs) * 70.0, 350.0)
            elif kind == 'vanish':
                evs.sort(key=lambda e: (e['at'][1], e['at'][0]))
                dur = BoardWidget.VANISH_MS + min(len(evs) * 60.0, 240.0)
            elif kind == 'merge':
                evs.sort(key=lambda e: (e['at'][1], e['at'][0]))
                dur = BoardWidget.POP_MS + min(len(evs) * 90.0, 360.0)
            elif kind == 'slide':
                evs.sort(key=lambda e: (-e['frm'][1], e['frm'][0]))
                dur = BoardWidget.SLIDE_MS + min(len(evs) * 25.0, 250.0)
            else:  # spawn
                rows = sorted({e['at'][1] for e in evs})
                rank = {r: i for i, r in enumerate(rows)}
                for e in evs:
                    e['delay'] = round(rank[e['at'][1]] * 110.0
                                       + e['at'][0] * 12.0)
                dur = (BoardWidget.SPAWN_MS
                       + (max(rank.values()) if rank else 0) * 110.0)
            res.append((kind, dur, evs))
        return res

    # --- согласование снимков ----------------------------------------------
    @staticmethod
    def _merge_variants(col):
        """Все состояния колонки, достижимые цепочкой движковых слияний
        (первая пара слева на каждом шаге) + compress. Список [(variant,
        steps), ...], где steps — vanish/merge/slide в координатах col."""
        variants = []
        cur = clean(col)
        chain = []
        while True:
            variants.append((dict(cur), list(chain)))
            ys = sorted(cur)
            pair = None
            for a, b in zip(ys, ys[1:]):
                if cur[a] == cur[b]:
                    pair = (a, b)
                    break
            if pair is None:
                break
            a, b = pair
            nv = cur[a] + 1
            chain.append(('vanish', (0, b), cur[b]))
            chain.append(('merge', (0, a), nv))
            cur[a] = nv
            del cur[b]
            compact = {}
            for ny, oy in enumerate(sorted(cur)):
                if ny != oy:
                    chain.append(('slide', (0, oy), (0, ny), cur[oy]))
                compact[ny] = cur[oy]
            cur = compact
        return variants

    @staticmethod
    def reconcile(before, after, src_x, dst_x, series):
        """Ход на КОПИИ может дополнительно схлопнуть колонку ИСТОЧНИКА
        (движок после add_row вызывает merge() для всего поля). Разрешаем
        честно: подбираем post-merge состояние колонки-источника из before,
        которое симуляция доведёт до after."""
        cb = clean(col_of(before, src_x))
        ca = clean(col_of(after, src_x))
        if cb == ca:
            return before
        for variant, _steps in MovePlanner._merge_variants(cb):
            new_before = dict(before)
            for y in list(col_of(new_before, src_x)):
                new_before.pop((src_x, y), None)
            for y, v in variant.items():
                new_before[(src_x, y)] = v
            if MovePlanner.simulate_move(new_before, after, src_x, dst_x,
                                         series) is not None:
                return new_before
        return before

    @staticmethod
    def reconcile_addrow(before, after):
        """add_row на копии мог схлопнуть ЛЮБЫЕ колонки (merge всего поля
        до/после доброса). Перебираем комбинации post-merge состояний колонок
        (с ограничением по размеру) и возвращаем before', для которого
        simulate_addrow сходится."""
        cols = [x for x in range(BOARD_COLS)
                if clean(col_of(before, x)) != clean(col_of(after, x))]
        if not cols:
            return before
        variants = {x: MovePlanner._merge_variants(col_of(before, x))[:6]
                    for x in cols}
        import itertools
        for combo in itertools.product(*variants.values()):
            new_before = {}
            for (x, y), v in before.items():
                new_before[(x, y)] = v
            for x, var in zip(cols, combo):
                for y in list(col_of(new_before, x)):
                    new_before.pop((x, y), None)
                for y, v in var.items():
                    new_before[(x, y)] = v
            if MovePlanner.simulate_addrow(new_before, after) is not None:
                return new_before
        return before

    # --- точная симуляция переноса ---------------------------------------
    @staticmethod
    def simulate_move(before, after, src_x, dst_x, series=None):
        cb_src = col_of(before, src_x)
        cb_dst = col_of(before, dst_x)
        ca_dst = col_of(after, dst_x)
        ca_src = col_of(after, src_x)

        ys = sorted(cb_src)
        cands = []
        if series:
            lo, hi = series
            seq = [y for y in ys if lo <= y <= hi]
            if seq and seq == list(range(seq[0], seq[-1] + 1)):
                cands.append(seq)
        else:
            for a in range(len(ys)):
                run = [ys[a]]
                for b in range(a + 1, len(ys)):
                    if ys[b] == run[-1] + 1:
                        run.append(ys[b])
                    else:
                        break
                cands.append(run)

        others_ok = all(clean(col_of(after, x)) == clean(col_of(before, x))
                        for x in range(BOARD_COLS)
                        if x not in (src_x, dst_x))

        for seq in cands:
            vals = [cb_src[y] for y in seq]
            h = len(clean(cb_dst))
            dest = [(dst_x, h + i) for i in range(len(vals))]
            steps = [{'op': 'fly', 'frm': (src_x, sy), 'to': to, 'v': v}
                     for sy, to, v in zip(seq, dest, vals)]
            col = clean(cb_dst)
            for i, v in enumerate(vals):
                col[h + i] = v
            msteps, col = MovePlanner.merge_col(dst_x, col)
            steps += msteps
            if not others_ok:
                continue
            rest_src = clean({y: v for y, v in cb_src.items()
                              if y not in seq})
            # колонка источника после хода могла быть сдвинута добросом
            # строк и/или схлопнута движком (merge всего поля при add_row).
            ok_src = (rest_src == clean(ca_src))
            extra_src = []
            if ok_src and clean(col) == clean(ca_dst):
                return steps
            # движок добавил строки (ход без слияний или цепочка добросов)
            for k in range(1, BOARD_ROWS * 2 + 1):
                grown = MovePlanner.growth_model(col, ca_dst, k)
                if grown is None:
                    continue
                gsteps, final = grown
                if clean(final) != clean(ca_dst):
                    continue
                ex = []
                if not ok_src:
                    mv = MovePlanner._src_after_growth(cb_src, seq, ca_src, k)
                    if mv is None:
                        continue
                    ex = mv
                return steps + gsteps + ex
        return None

    @staticmethod
    def _src_after_growth(src_col, seq, ca_src, k):
        """Колонка-источник в переходе с добросом k строк: сначала cascade
        на k (все оставшиеся карты съезжают вниз), затем движковые merge
        всего поля поверх. Возвращает список шагов либо None."""
        H = BOARD_ROWS * 2 + 1
        rest = clean({y: v for y, v in src_col.items() if y not in seq})
        ca = clean(ca_src)
        if any(y + k >= H for y in rest):
            return None
        pre = [{'op': 'slide', 'frm': (0, y), 'to': (0, y + k), 'v': v}
               for y, v in sorted(rest.items(), reverse=True)]
        shifted = {y + k: v for y, v in rest.items()}
        if shifted == ca:
            return pre
        msteps, final = MovePlanner.merge_col(0, dict(shifted))
        if clean(final) == ca:
            return pre + msteps
        return None

    @staticmethod
    def merge_col(x, col):
        """Game._merge + _compress для одной колонки; возвращает шаги."""
        steps = []
        changed = True
        while changed:
            changed = False
            ys = sorted(clean(col))
            for a, b in zip(ys, ys[1:]):
                if col[a] == col[b]:
                    nv = col[a] + 1
                    steps.append({'op': 'vanish', 'at': (x, b), 'v': col[b]})
                    steps.append({'op': 'merge', 'at': (x, a), 'v': nv})
                    col[a] = nv
                    del col[b]
                    changed = True
                    break
        ys = sorted(clean(col))
        newcol = {}
        for ny, oy in enumerate(ys):
            v = col[oy]
            if ny != oy:
                steps.append({'op': 'slide', 'frm': (x, oy), 'to': (x, ny),
                              'v': v})
            newcol[ny] = v
        return steps, newcol

    @staticmethod
    def growth_model(cur_col, ca_dst, k=None):
        """Модель «движок добавил k строк сверху + merge всего поля» для
        колонки-цели. Возвращает (steps, final_col) либо None.

        cur_col — состояние колонки до доброса (после merge цели);
        ca_dst  — итоговая колонка в after; k — фиксированное число строк
        (если не задано — перебираем все).
        """
        cur = clean(cur_col)
        H = BOARD_ROWS * 2 + 1
        ks = [k] if k is not None else range(1, H)
        for kk in ks:
            k = kk
            ok = True
            shifted = {}
            for y, v in cur.items():
                ny = y + k
                if ny < H and ca_dst.get(ny) == v:
                    shifted[ny] = v
                else:
                    ok = False
                    break
            if not ok:
                continue
            leftover = {y: v for y, v in ca_dst.items()
                        if v and y not in shifted}
            if not leftover:
                continue
            grown = dict(shifted)
            grown.update(leftover)
            extra, final = MovePlanner.merge_col(0, dict(grown))
            if clean(final) != clean(ca_dst):
                continue
            steps = []
            # cascade: старые карты съезжают вниз на k
            for y, v in sorted(cur.items(), reverse=True):
                steps.append({'op': 'slide', 'frm': (0, y), 'to': (0, y + k),
                              'v': v})
            popped = {tuple(s['at']) for s in extra if s['op'] == 'merge'}
            for y, v in sorted(leftover.items()):
                if (0, y) in popped:
                    continue
                steps.append({'op': 'spawn', 'at': (0, y), 'v': v})
            steps += extra
            return steps, final
        return None

    # --- симуляция add_row ------------------------------------------------
    @staticmethod
    def simulate_addrow(before, after):
        """add_row: движок мог добавить несколько строк подряд (цепочка
        добросов, пока на поле есть пары). Восстанавливаем число строк k и
        пост-слияния каждой колонки; всё сходится клетка-в-клетку, иначе
        PlanningError."""
        H = BOARD_ROWS * 2 + 1
        for k in range(1, H):
            ok = True
            per_col_new = {}
            for x in range(BOARD_COLS):
                cb = clean(col_of(before, x))
                ca = clean(col_of(after, x))
                shifted = {}
                for y, v in cb.items():
                    ny = y + k
                    if ny < H and ca.get(ny) == v:
                        shifted[ny] = v
                    else:
                        ok = False
                        break
                if not ok:
                    break
                per_col_new[x] = {y: v for y, v in ca.items()
                                  if y not in shifted}
            if not ok:
                continue
            if any(y >= k for x in range(BOARD_COLS) for y in per_col_new[x]):
                continue
            if not any(per_col_new[x] for x in range(BOARD_COLS)):
                continue
            steps = []
            good = True
            for x in range(BOARD_COLS):
                cb = clean(col_of(before, x))
                for y, v in sorted(cb.items(), reverse=True):
                    steps.append({'op': 'slide', 'frm': (x, y),
                                  'to': (x, y + k), 'v': v})
                grown = {y + k: v for y, v in cb.items()}
                grown.update(per_col_new[x])
                extra, final = MovePlanner.merge_col(x, dict(grown))
                if clean(final) != clean(col_of(after, x)):
                    good = False
                    break
                popped = {tuple(s['at']) for s in extra if s['op'] == 'merge'}
                for y, v in sorted(per_col_new[x].items()):
                    if (x, y) in popped:
                        continue
                    steps.append({'op': 'spawn', 'at': (x, y), 'v': v})
                steps += extra
            if not good:
                continue
            return steps
        return None


# ---------------------------------------------------------------------------
# Виджет игрового поля
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    """Рисует поле Game и обрабатывает клики/drag-and-drop."""

    FLY_MS = 430.0
    VANISH_MS = 300.0
    POP_MS = 260.0
    SLIDE_MS = 300.0
    SPAWN_MS = 380.0
    TICK_MS = 16

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected = None        # (x, y_lo, y_hi) — выбранная серия
        self.hover_col = None
        # drag-and-drop
        self.dragging = False
        self.drag_pos = None        # QPointF курсора во время перетаскивания
        self._press_cell = None     # (x, y) под курсором в момент press
        self._press_pos = None
        self._press_moved = False
        self._drag_series = None    # (lo, hi) — серия, которую тянем

        # плеер анимаций
        self.anim_active = False
        self._phases = []           # [(kind, dur_ms, [events])]
        self._pi = 0
        self._pt = 0.0
        self._tl_total = 0.0
        self._base = {}
        self._after = {}
        self._carry = set()
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []
        self._pending_gameover = None
        self._planning_error = None
        # журнал последних планировочных ошибок (для диагностики извне)
        self.planning_log = []

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
        """Центр клетки (x, y). y = 0 — верхний видимый ряд (как в движке:
        новые строки появляются сверху и сдвигают карты вниз)."""
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
        """y занятых ВИДИМЫХ клеток колонки (y < size[1])."""
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
        """Привести клетку (x, y) к занятой карте колонки (ближайшей снизу)."""
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
        try:
            res, added = g.action_full((sx, sy), dx, count=cnt)
        except Exception:
            return "Invalid position", False
        if isinstance(res, tuple):
            res = str(res[0])
        return res, added

    def _try_move_probe(self, sx, sy, dx, cnt):
        """Прогон хода на независимой копии. None — ход невозможен."""
        gc = self._clone()
        res, added = self._engine_move(gc, sx, sy, dx, cnt)
        if res == "Invalid position":
            return None
        return res, gc, added

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
            res = gc.add_row()
        except Exception:
            return False
        if isinstance(res, tuple):
            return False
        if res is not None and str(res).startswith("Game Over"):
            return False
        return not any(gc.field[x][self.game.size[1]].value
                       for x in range(self.game.size[0]))

    def has_any_action(self):
        win = self.window()
        if win is not None and getattr(win, "game_over", False):
            return False
        if self.legal_moves():
            return True
        return self.add_row_survives()

    # --- выполнение хода ---------------------------------------------------
    def try_move(self, src_x, src_y, dst_x, count_rows=None):
        """Перенос серии; возвращает True, если ход был совершён."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return False
        if count_rows is None:
            count_rows = 1
        if src_x == dst_x or count_rows <= 0:
            self.selected = None
            self.update()
            return False
        probe = self._try_move_probe(src_x, src_y, dst_x, count_rows)
        if probe is None:
            self.selected = None
            win.show_status("Движок отвергает такой перенос "
                            "(источник пуст или позиция недопустима).",
                            warn=True)
            self.update()
            return False
        res_probe, gc_probe, added_probe = probe
        before = self.snapshot(self.game)
        series = (src_y, src_y + count_rows - 1)

        if res_probe.startswith("Game Over"):
            # Голый движок на таком ходе оставляет финальное поле и возвращает
            # "Game Over..." — делаем ровно то же самое.
            win.push_undo()
            after = self.snapshot(gc_probe)
            self.game = gc_probe
            win.board_game_changed(self.game)
            self.selected = None
            self._play(before, after, src_x, dst_x, series,
                       gameover_msg=res_probe)
            win.refresh_stats()
            return True

        # Success на копии — применяем к оригиналу. Если движок НЕ добавлял
        # строку, ход детерминирован и результат совпадает с голым движком;
        # если добавил (там randint) — финалом берём проверенную копию,
        # чтобы экран показывал ровно предсказанный исход.
        win.push_undo()
        real_res, real_added = self._engine_move(self.game, src_x, src_y,
                                                 dst_x, count_rows)
        msg = None
        if real_res != "Success":
            after = self.snapshot(gc_probe)
            self.game = gc_probe
            win.board_game_changed(gc_probe)
            msg = str(real_res)
        else:
            added = bool(real_added) or bool(added_probe)
            if added:
                same = (self.snapshot(self.game) == self.snapshot(gc_probe)
                        and self.game.points == gc_probe.points)
                if not same:
                    self.game = gc_probe
                    win.board_game_changed(gc_probe)
            after = self.snapshot(self.game)

        self.selected = None
        combo = max(self.game.lastCombo) if self.game.lastCombo else 0
        if msg is None:
            win.show_status(f"Комбо x{combo}!" if combo > 1 else
                            f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        self._play(before, after, src_x, dst_x, series, gameover_msg=msg)
        if msg is None:
            win.check_deadlock()
        return True

    def do_add_row(self):
        """«+ Строка»: прогон на независимой копии, затем анимация всего
        произошедшего (движок может добавить несколько строк сразу)."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return
        probe = self._clone()
        try:
            res = probe.add_row()
        except Exception:
            res = "Game Over: engine exception"
        bad = (isinstance(res, tuple) or
               (res is not None and str(res).startswith("Game Over")))
        before = self.snapshot(self.game)
        after = self.snapshot(probe)
        win.push_undo()
        self.game = probe
        win.board_game_changed(probe)
        self.selected = None

        msg = None
        if bad:
            msg = str(res) if res is not None else "строка заполнила поле"
        else:
            win.show_status("Добавлена новая строка сверху.")
        win.refresh_stats()
        self._play(before, after, None, None, None, gameover_msg=msg)
        if not bad:
            win.check_deadlock()

    # --- плеер анимаций -----------------------------------------------------
    def _play(self, before, after, src_x, dst_x, series, gameover_msg=None):
        try:
            steps = MovePlanner.plan(before, after, src_x, dst_x, series)
        except PlanningError as exc:
            steps = []
            self._planning_error = str(exc)
            self.planning_log.append(
                f"{exc} | before={sorted(before.items())} "
                f"after={sorted(after.items())} move={src_x}->{dst_x}")
            if len(self.planning_log) > 20:
                self.planning_log.pop(0)
        self._phases = MovePlanner.phases(steps)
        self._pi = 0
        self._pt = 0.0
        self._tl_total = sum(d for _, d, _ in self._phases)
        self._base = before
        self._after = after
        self._pending_gameover = gameover_msg
        self._reset_phase_state()
        if not self._phases:          # анимировать нечего — мгновенно
            self._finish_anim()
            return
        self.anim_active = True
        if not self._anim.isActive():
            self._anim.start()
        self._build_frame()
        self.update()

    def _reset_phase_state(self):
        self._carry = set()
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []

    def _fix_carry_phase(self):
        """Фаза завершена: её карты больше не рисуем базовым слоем."""
        kind, _dur, evs = self._phases[self._pi]
        for ev in evs:
            if kind == 'fly':
                self._carry.add(ev['frm'])
                self._carry.add(ev['to'])
            elif kind == 'vanish':
                self._carry.add(ev['at'])
            elif kind == 'pop':
                self._carry.add(ev['at'])
            elif kind == 'slide':
                self._carry.add(ev['frm'])
                self._carry.add(ev['to'])
            elif kind == 'spawn':
                self._carry.add(ev['at'])

    def _build_frame(self):
        """Собирает события текущего кадра из активной фазы."""
        self._reset_phase_state()
        if not (0 <= self._pi < len(self._phases)):
            return
        # всё, что зафиксировано в завершённых фазах, остаётся скрытым
        for pi in range(self._pi):
            kind, _dur, evs = self._phases[pi]
            for ev in evs:
                if kind == 'fly':
                    self._carry.add(ev['frm']); self._carry.add(ev['to'])
                elif kind == 'slide':
                    self._carry.add(ev['frm']); self._carry.add(ev['to'])
                else:
                    self._carry.add(ev['at'])
        kind, dur, evs = self._phases[self._pi]
        for ev in evs:
            delay = ev.get('delay', 0.0)
            uu = (self._pt - delay) / dur
            if uu < 0.0:
                # событие ещё не стартовало: летящие карты остаются на
                # СТАРЫХ местах (иначе кадр «прыгает»)
                if kind == 'fly':
                    self._flights.append((ev['frm'], ev['frm'], ev['v'], 0.0))
                    self._carry.add(ev['frm'])
                continue
            uu = min(1.0, uu)
            if kind == 'fly':
                self._flights.append((ev['frm'], ev['to'], ev['v'], uu))
                self._carry.add(ev['frm'])
                if uu >= 1.0:
                    self._carry.add(ev['to'])
            elif kind == 'vanish':
                self._vanishing.append((ev['at'], ev['v'], uu))
                if uu >= 1.0:
                    self._carry.add(ev['at'])
            elif kind == 'pop':
                self._popping.append((ev['at'], ev['v'], uu))
                self._carry.add(ev['at'])
            elif kind == 'slide':
                self._sliding.append((ev['frm'], ev['to'], ev['v'], uu))
                self._carry.add(ev['frm'])
                if uu >= 1.0:
                    self._carry.add(ev['to'])
            elif kind == 'spawn':
                self._spawning.append((ev['at'], ev['v'], uu))
                self._carry.add(ev['at'])

    def _on_anim_tick(self):
        if not self.anim_active:
            return
        self._pt += self.TICK_MS
        while self._pi < len(self._phases) and \
                self._pt >= self._phases[self._pi][1]:
            self._fix_carry_phase()
            self._pt -= self._phases[self._pi][1]
            self._pi += 1
        self._build_frame()
        self.update()
        if self._pi >= len(self._phases):
            self._finish_anim()

    def _finish_anim(self):
        self.anim_active = False
        self._phases = []
        self._pi = 0
        self._pt = 0.0
        self._reset_phase_state()
        self._base = {}
        self._after = {}
        self._anim.stop()
        win = self.window()
        if self._pending_gameover is not None and win is not None:
            win.on_game_over(self._pending_gameover)
        self._pending_gameover = None
        if self._planning_error and win is not None:
            win.show_status("Ошибка планирования анимации: "
                            + self._planning_error, warn=True)
            self._planning_error = None
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
            # Shift+клик: выбрать ВЕСЬ столбец
            self.selected = (x, occ[0], occ[-1])
            self._announce_selection()
            self.update()
            return
        if prev is not None and prev[0] != x:
            # второй клик по ДРУГОЙ колонке = перенос выбранной серии сюда
            self.try_move(prev[0], prev[1], x, self.series_count(prev))
            return
        if prev is not None and prev[0] == x:
            if prev[1] < hit <= prev[2] or (hit == prev[1] and
                                            prev[1] == prev[2]):
                if prev[1] == hit == prev[2]:
                    self.selected = None       # клик по одиночной карте — снять
                else:
                    # клик ВНУТРИ серии (кроме верхнего края) — ничего не
                    # меняем, готовим drag текущей серии
                    self._drag_series = (prev[1], prev[2])
                self.update()
                return
            if hit == prev[1]:
                # повторный клик по верхнему краю — снять выбор
                self.selected = None
                self.update()
                return
        # обычный выбор: серия начинается с hit и идёт до конца стопки
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
                    # старт drag: тянем выбранную серию (или ту, что
                    # захватили прямо в точке нажатия)
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
            self.update()      # обычный клик уже обработан в pressEvent
            return
        sx = press_x
        if sx is None or self._drag_series is None:
            self.update()
            return
        tx = self._col_at(QPointF(drop_pos.x(), PAD + 1))
        if tx is None or tx == sx:
            self.update()      # бросок в свою колонку — выбор остаётся
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
        """Кадр анимированного перехода между _base и _after."""
        moving_frm = {f for f, _t, _v, _u in self._flights}
        sliding_frm = {f for f, _t, _v, _u in self._sliding}
        vanish_at = {a for a, _v, _u in self._vanishing}
        pop_at = {a for a, _v, _u in self._popping}
        spawn_at = {a for a, _v, _u in self._spawning}

        # статичные карты целевого состояния
        for (x, y), v in self._after.items():
            if not v or y < 0 or y >= self.game.size[1]:
                continue
            if (x, y) in (spawn_at | pop_at | moving_frm | sliding_frm
                          | vanish_at | self._carry):
                continue
            self._draw_card(p, self.cell_rect(x, y), v)

        # cascade/slide: карты едут между позициями
        for frm, to, v, u in self._sliding:
            e = ease_inout(u)
            c1, c2 = self.cell_center(*frm), self.cell_center(*to)
            cy = c1.y() + (c2.y() - c1.y()) * e
            rect = QRectF(c1.x() - CELL / 2, cy - CELL / 2, CELL, CELL)
            if rect.bottom() > PAD and rect.top() < self.board_px_h() - PAD:
                self._draw_card(p, rect, v)

        # схлопывающиеся карты
        for at, v, u in self._vanishing:
            e = ease_inout(u)
            sc = 1.0 - 0.92 * e
            al = int(255 * (1.0 - e))
            if sc > 0.05 and al > 0:
                self._draw_card(p, self.cell_rect(*at), v, scale=sc, alpha=al)

        # полёты: летящая карта + силуэт на старом месте
        for frm, to, v, u in self._flights:
            e = ease_inout(u)
            c1, c2 = self.cell_center(*frm), self.cell_center(*to)
            arc = -min(abs(c2.x() - c1.x()), 140.0) * 0.35 * \
                (4.0 * e * (1.0 - e)) if c2.x() != c1.x() else 0.0
            cx = c1.x() + (c2.x() - c1.x()) * e
            cy = c1.y() + (c2.y() - c1.y()) * e + arc
            self._draw_card(p, QRectF(c1.x() - CELL / 2, c1.y() - CELL / 2,
                                      CELL, CELL), v,
                            alpha=int(60 * (1.0 - e)))
            self._draw_card(p, QRectF(cx - CELL / 2, cy - CELL / 2,
                                      CELL, CELL), v,
                            scale=1.0 + 0.06 * (1.0 - abs(0.5 - e) * 2.0))

        # pop результата слияний
        for at, v, u in self._popping:
            e = ease_out(u)
            sc = 0.35 + 0.65 * e
            if u < 0.55:
                sc *= 1.0 + 0.18 * (u / 0.55)
            self._draw_card(p, self.cell_rect(*at), v, scale=sc,
                            flash=int(350 * (1.0 - e)))

        # въезд новых карт/строк сверху (все добавленные строки)
        for at, v, u in self._spawning:
            e = ease_out(u)
            x, y = at
            cy_target = PAD + y * (CELL + GAP) + CELL / 2
            start_y = -CELL / 2
            cy = start_y + (cy_target - start_y) * e
            rect = QRectF(PAD + x * (CELL + GAP), cy - CELL / 2, CELL, CELL)
            if y >= self.game.size[1]:   # умершая за краем — гаснет
                self._draw_card(p, rect, v, alpha=int(120 * (1.0 - e)))
            else:
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

    def pop_undo_discard(self):
        if self._undo_stack:
            self._undo_stack.pop()

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
        self.board._phases = []
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


+++ main.py (修改后)
# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) для игры "mergeCards" на PyQt5.

Игровая логика — mergeCards/API.py (класс Game). GUI использует движок
как есть, но честно сообщает игроку исход каждого хода до его совершения.

Как устроен движок (важно для понимания правил):
    - y = 0 — верхний ряд поля; новые строки появляются СВЕРХУ и сдвигают
      все карты вниз; карты, упавшие за нижний край (y >= size[1]), — смерть;
    - перенос забирает выбранную карту и ВСЁ, что ниже неё в колонке, и
      кладёт пачку СНИЗУ (под нижнюю карту) колонки-цели;
    - после переноса, если слияний не было, движок сам добавляет новую
      строку; если после этого нет ни одной пары одинаковых карт на всём
      поле — он продолжает добавлять строки, пока поле не умрёт.
      Поэтому «бессмыслие» ходы реально могут кончиться прямо в Game Over;
    - add_row НЕ вызывает merge() — это просто доброс строки + проверка
      переполнения (как в голом API).

Управление мышью (перенести МОЖНО любой «срез» колонки — карту и всё под
ней, а не обязательно весь столбец):
    - клик по карте             — выбрать именно эту карту (верх среза);
    - клик по другой колонке    — перенести выбранный срез туда;
    - drag (зажать и тащить)   — то же одним жестом;
    - ↑ / ↓ или колесо мыши     — двигать верхнюю границу среза по стопке
      (т.е. выбирать, сколько карт снизу колонки перенести);
    - Shift+клик                — выбрать ВЕСЬ столбец;
    - ← / →                     — с клавиатуры: перенести срез в соседнюю
      колонку (если выбор уже сделан), иначе — выбрать колонку;
    - Пробел                    — добавить строку сверху (add_row);
    - Z                         — отменить последний ход;
    - R                         — новая игра;  Esc — выход.

Честный проигрыш: исход каждого хода предсказывается честным прогоном на
независимой копии движка (тот же мешок случайных чисел, что и у оригинала).
Если копия вернула «Game Over...», GUI применяет этот финальный ход к игре
и объявляет поражение — ровно так, как это делает голый движок без всяких
предупреждений-подтверждений. Игра завершается также, когда не осталось ни
одного хода «Success» и «+ Строка» тоже убивает поле.
"""

import copy
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mergeCards"))

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer  # noqa: E402
from PyQt5.QtGui import (  # noqa: E402
    QColor, QFont, QCursor, QPainter, QLinearGradient, QPainterPath, QPen,
)
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)

try:  # используем API из mergeCards/
    from API import Game, Card, pretty_number  # noqa: E402
except Exception as exc:  # pragma: no cover
    print("Не удалось импортировать mergeCards/API.py:", exc)
    raise

# ---------------------------------------------------------------------------
# Константы оформления
# ---------------------------------------------------------------------------

BOARD_COLS = 6          # ширина поля (колонок)
BOARD_ROWS = 7          # высота поля (строк)
CELL = 92               # размер клетки, px
GAP = 10                # зазор между картами, px
PAD = 16                # отступ поля от края

BG_TOP = QColor("#141a2b")
BG_BOTTOM = QColor("#0b0e18")

# Базовая палитра «уровней» карты (value -> цвет), как в 2048-подобных играх
LEVEL_COLORS = [
    ("#3d4663", "#cfe0ff"),  # 0  (не используется, заглушка)
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
    """Цвет и текст карты для уровня value (с лёгким дрейфом оттенка выше 10)."""
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
    """Текст на карте: 2**value; для больших чисел — сокращение из pretty_number."""
    n = 1 << value
    s = str(pretty_number(n))
    if len(s) > 9:                     # очень большие — показательный вид
        mant, _, exp = s.partition(" * 10^")
        if exp:
            return f"10^{int(exp)}"
    return s


# ---------------------------------------------------------------------------
# Плавные кривые (ease-out cubic) и тайминги анимаций, мс
# ---------------------------------------------------------------------------

def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


MOVE_MS = 260      # полёт переносимой пачки в колонку-цель
DROP_MS = 240      # падение карт после слияния/добора
POP_MS = 300       # «пульс» слитой карты
ROW_MS = 340       # въезд НОВОЙ строки + сдвиг всего поля вниз (каждый слой)
DEAL_MS = 520      # раздача стартовых строк при новой игре
STAGGER_MS = 45    # задержка между картами одной пачки / слоями строки


# ---------------------------------------------------------------------------
# Виджет игрового поля
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    """Рисует поле Game и обрабатывает клики/drag-and-drop колонок.

    Анимации (построитель keyframe-цепочек self._plan):
      - перенос пачки летит по дуге в колонку-цель;
      - карты внутри пачки схлопываются в слитые (карта-«призрак» летит к
        партнёру и исчезает, результат сливается с pop-пульсом);
      - остальные карты плавно дожимаются вниз (compress движка);
      - КАЖДЫЙ добавленный движком слой въезжает сверху своим отдельным
        параллельным слоем анимации (не только последний);
      - drag-and-drop: призрак пачки следует за курсором, при отпускании
        ход исполняется той же анимацией переноса.
    """

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected = None              # (x, y) — выбранная карта-источник
        self.hover_col = None             # колонка под курсором
        self.drag_from = None             # (x, y) — откуда начали перетаскивание
        self.drag_all_column = False      # тянем весь столбец?
        self.drag_pos = None              # позиция курсора во время перетаскивания

        # --- состояние анимаций -------------------------------------
        self._t = 0                       # часы анимации, мс
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._base_vals = {}              # (x, y) -> значение ДО хода
        self._plan = []                   # [(t0, dur, kind, data)]
        self._pop_at = {}                 # (col, rank) -> t0 пульса слияния
        self._deal_t0 = None              # старт раздачи (новая игра)
        self._animating = False           # блокирует ввод на время роли
        self._queue = []                  # ходы, принятые во время анимации
        self._after_cb = None             # колбэк по окончании всей цепочки

        self.setMinimumSize(self.board_px_w(), self.board_px_h())
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # --- размеры -----------------------------------------------------
    def board_px_w(self):
        return PAD * 2 + self.game.size[0] * CELL + (self.game.size[0] - 1) * GAP

    def board_px_h(self):
        return PAD * 2 + self.game.size[1] * CELL + (self.game.size[1] - 1) * GAP

    def col_rect(self, x):
        return QRectF(PAD + x * (CELL + GAP), PAD,
                      CELL, self.board_px_h() - 2 * PAD)

    def cell_center(self, x, y):
        """Центр клетки (x, y) в координатах API.

        В API y = 0 — это ВЕРХНИЙ видимый ряд: именно туда движок добавляет
        новые строки (_addrow сдвигает все карты вниз, к большему y).
        Значит на экране row_экрана == y_api (без инверсии).
        """
        cx = PAD + x * (CELL + GAP) + CELL / 2
        cy = PAD + y * (CELL + GAP) + CELL / 2
        return QPointF(cx, cy)

    def cell_rect(self, x, y):
        c = self.cell_center(x, y)
        return QRectF(c.x() - CELL / 2, c.y() - CELL / 2, CELL, CELL)

    def _col_at(self, pos: QPointF):
        for x in range(self.game.size[0]):
            if self.col_rect(x).contains(pos):
                return x
        return None

    # --- состояние поля ----------------------------------------------
    def top_index(self, x):
        """y самой ВЕРХНЕЙ занятой клетки колонки (None, если колонка пуста)."""
        for y in range(self.game.size_calc[1]):
            if self.game.field[x][y].value:
                return y
        return None

    def bottom_index(self, x):
        """y самой НИЖНЕЙ занятой клетки колонки (None, если колонка пуста)."""
        b = None
        for y in range(self.game.size_calc[1]):
            if self.game.field[x][y].value:
                b = y
        return b

    def column_height(self, x):
        t = self.top_index(x)
        return 0 if t is None else self.bottom_index(x) - t + 1

    def value_at(self, x, y):
        if 0 <= x < self.game.size[0] and 0 <= y < self.game.size_calc[1]:
            return self.game.field[x][y].value
        return 0

    def visible_cards(self, x):
        """y-координаты только ВИДИМЫХ карт колонки (движок хранит карты и
        за нижним краем поля — их показывать и выбирать нельзя)."""
        return [y for y in range(self.game.size[1]) if self.value_at(x, y)]

    def selected_count(self):
        """Сколько карт будет перенесено при текущем выборе.

        Движок переносит выбранную карту и ВСЁ под ней до конца стопки
        (action_full собирает карты от posFrom.y до низа), поэтому, чтобы
        перенести НЕ весь столбец, надо выбрать карту не самую верхнюю.
        """
        if self.selected is None:
            return 0
        x, y = self.selected
        cnt = 0
        yy = y
        while self.value_at(x, yy):
            cnt += 1
            yy += 1
        return cnt

    def slice_count(self, x, y):
        """Сколько карт уйдёт при выборе карты (x, y): она и всё под ней."""
        cnt, yy = 0, y
        while self.value_at(x, yy):
            cnt += 1
            yy += 1
        return cnt

    def stack_height(self, x):
        return len(self.visible_cards(x))

    # --- проверка ходов через движок (на копиях!) ----------------------
    def _clone(self) -> Game:
        """Полноценный независимый клон игры.

        ВАЖНО: у mergeCards/API.Game.copy() есть особенность — _bag
        передаётся ПО ССЫЛКЕ, и его мутация в копии портит оригинал
        (и наоборот). Поэтому пересобираем игру из сериализованного
        состояния с НЕЗАВИСИМЫМ списком _bag.
        """
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

    def _try_move(self, src_x, src_y, dst_x, count_rows):
        """Прогоняет ход через КОПИЮ движка.

        Возвращает None, если ход невозможен (Invalid position / падение),
        или ('Success'|'Game Over...', gcopy, added_row) — результат на копии.
        Оригинал никогда не мутируется.
        """
        try:
            gc = self._clone()
            res, added = gc.action_full((src_x, src_y), dst_x, count=count_rows)
        except Exception:
            return None
        if isinstance(res, tuple):           # маловероятно, но подстрахуемся
            res = str(res[0])
        if res == "Invalid position":
            return None
        return res, gc, added

    def legal_moves(self):
        """Все варианты переноса, которые движок принимает.

        Элемент: (src_x, src_y, dst_x, count, result)
        result == 'Success' либо 'Game Over...' — второй вариант ходом НЕ
        считается (см. has_any_action). Перебираются только видимые карты.
        """
        out = []
        for sx in range(self.game.size[0]):
            vis = self.visible_cards(sx)
            if not vis:
                continue
            t = vis[0]
            for sy in vis:
                cnt = self.selected_count_at(sx, sy)
                for dx in range(self.game.size[0]):
                    if dx == sx:
                        continue
                    r = self._try_move(sx, sy, dx, cnt)
                    if r is None:
                        continue
                    res, gc, added = r
                    out.append((sx, sy, dx, cnt, res))
        return out

    def selected_count_at(self, x, y):
        """Сколько карт уйдёт при выборе карты (x, y)."""
        cnt, yy = 0, y
        while self.value_at(x, yy):
            cnt += 1
            yy += 1
        return cnt

    def add_row_survives(self):
        """Выживет ли игра после «+ Строка» (проверяется на копии).

        Движок внутри add_row сам добрасывает строки, пока на поле есть
        хотя бы одна пара одинаковых карт (_can_merge), и может выдать
        Game Over уже в процессе. Клон полностью независим (свой _bag),
        поэтому оригинал не портится."""
        try:
            gc = self._clone()
            res = gc.add_row()
        except Exception:
            return False
        if isinstance(res, tuple):
            return False
        if res is not None and str(res).startswith("Game Over"):
            return False
        return not any(gc.field[x][self.game.size[1]].value
                       for x in range(self.game.size[0]))

    def has_any_action(self):
        if self.game_over_flag():
            return False
        # Любое действие, которое движок ещё не запретил, — это ход: и
        # «Success», и предсказанный «Game Over...» (последний честно
        # завершает партию сразу при выполнении, см. try_move).
        if self.legal_moves():
            return True
        return self.add_row_survives()

    def game_over_flag(self):
        win = self.window()
        return bool(win is not None and getattr(win, "game_over", False))

    # --- выполнение хода ----------------------------------------------
    def try_move(self, src_x, src_y, dst_x, count_rows=None):
        """Совершает перенос; возвращает True, если ход принят/запланирован."""
        win = self.window()
        if win is None:
            return False
        if getattr(win, "game_over", False):
            return False
        if self._animating:
            # очередь: не более одного отложенного хода — поле не убегает
            # вперёд игрока, но клик во время анимации не теряется
            if len(self._queue) < 1:
                self._queue.append((src_x, src_y, dst_x, count_rows))
            return True
        if count_rows is None:
            count_rows = self.slice_count(src_x, src_y) or 1
        if src_x == dst_x or count_rows <= 0:
            self.selected = None
            self.update()
            return False
        gc_probe = self._clone()
        try:
            res_probe, added_probe = gc_probe.action_full(
                (src_x, src_y), dst_x, count=count_rows)
        except Exception:
            res_probe, added_probe = "Invalid position", False
        if isinstance(res_probe, tuple):           # подстрахуемся
            res_probe = str(res_probe[0])
        if res_probe == "Invalid position":
            self.selected = None
            win.show_status("Движок отвергает такой перенос "
                            "(источник пуст или позиция недопустима).",
                            warn=True)
            self.update()
            return False

        # Снимок ДО хода + keyframe-план переноса (в координатах до хода)
        snapshot = self._snapshot()
        pre = self._precompute(snapshot, src_x, src_y, dst_x, count_rows)

        # Голый движок на предсказанном Game Over-ходе просто оставляет
        # финальное состояние — делаем ровно то же, без «подтверждений».
        if res_probe.startswith("Game Over"):
            win.push_undo()
            self.game = gc_probe
            win.board_game_changed(self.game)
            win.on_game_over(res_probe)
            win.refresh_stats()
            self._play_state(snapshot, gc_probe, pre)
            return True

        # Ход «Success»: применяем к ЧИСТОМУ оригиналу. Если движок НЕ
        # добавлял строку, он детерминирован и результат совпадает с голым
        # движком клетка-в-клетку; если добавил (внутри randint) — берём
        # проверенную копию, экран показывает ровно предсказанный исход.
        win.push_undo()
        try:
            real_res, real_added = self.game.action_full(
                (src_x, src_y), dst_x, count=count_rows)
        except Exception:
            real_res, real_added = "Game Over: engine exception", False
        if isinstance(real_res, tuple):
            real_res = str(real_res[0])
        if real_res != "Success":
            final = gc_probe
            added = added_probe
        else:
            added = real_added or added_probe
            final = self.game
            if added and added_probe:
                same = (all(final.field[x][y].value == gc_probe.field[x][y].value
                            for x in range(final.size[0])
                            for y in range(final.size_calc[1]))
                        and final.points == gc_probe.points)
                if not same:
                    final = gc_probe
        self.selected = None
        win.board_game_changed(final)
        combo = max(final.lastCombo) if final.lastCombo else 0
        win.show_status(f"Комбо x{combo}!" if combo > 1 else
                        f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        self._play_state(snapshot, final, pre)
        win.check_deadlock()
        return True

    def _drain_queue(self):
        while self._queue:
            args = self._queue.pop(0)
            self.try_move(*args)
            break   # следующий — уже после новой анимации

    # --- снимки состояния и keyframe-план -------------------------------
    def _snapshot(self):
        """Снимок поля + колонок (в терминах API: y=0 верх, стопки вниз)."""
        return self._state_of(self.game)

    @staticmethod
    def _state_of(g: Game):
        field = {(x, y): g.field[x][y].value
                 for x in range(g.size[0]) for y in range(g.size_calc[1])}
        cols = {}
        for x in range(g.size[0]):
            col = []
            y = 0
            while y < g.size_calc[1] and field.get((x, y)):
                col.append(field[(x, y)])
                y += 1
            cols[x] = col
        return {"field": field, "cols": cols, "size": g.size,
                "points": g.points}

    @staticmethod
    def _col_slot(x, i, size):
        """Экранный центр i-й карты колонки x (i=0 — верхняя). Ранги за
        нижним краем уходят за доску — так видно «падение» за край."""
        cx = PAD + x * (CELL + GAP) + CELL / 2
        cy = PAD + i * (CELL + GAP) + CELL / 2
        return QPointF(cx, cy)

    def _precompute(self, snap, sx, sy, dx, cnt):
        """Ключевые кадры переноса/слияний — все координаты «до хода».

        Эмулирует то, что сделает движок (детерминированная часть хода):
        пачка кладётся снизу цели, затем жадное схлопывание пар снизу вверх.

        Важно: движок в _merge сканирует колонку СВЕРХУ ВНИЗ и на каждом
        проходе сливает карту с её нижним соседом, после чего _compress
        дожимает стопку вниз; процесс повторяется до устойчивости. Прямая
        эмуляция этого процесса даёт точные пары (индексы в собранной
        стопке) и итоговую колодку — по ней и строятся keyframe'ы.
        """
        size = snap["size"]
        cols = {x: list(snap["cols"][x]) for x in snap["cols"]}
        moved = cols[sx][sy:sy + cnt]
        cols[sx] = cols[sx][:sy] + cols[sx][sy + cnt:]
        insert_at = len(cols[dx])
        stack = cols[dx] + moved
        # --- эмуляция _merge+_compress (сверху вниз, повторными проходами)
        cells = [[v, k] for k, v in enumerate(stack)]     # [value, orig_idx]
        pairs = []                                        # (loser_idx, keeper_idx)
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(cells) - 1:
                if cells[i][0] == cells[i + 1][0]:
                    pairs.append((cells[i + 1][1], cells[i][1]))
                    cells[i][0] += 1                      # верхняя карта растёт
                    del cells[i + 1]                      # нижняя исчезает
                    changed = True
                    i += 1
                else:
                    i += 1
        final_stack = [c[0] for c in cells]
        move_frames = []
        for i, v in enumerate(moved):
            start = self._col_slot(sx, sy + i, size)
            end = self._col_slot(dx, insert_at + i, size)
            arc = min(abs(end.x() - start.x()) * 0.35, 90.0) + 14.0
            move_frames.append({"value": v, "start": start, "end": end,
                                "arc": arc, "delay": i * STAGGER_MS})
        pair_frames = []
        for loser, keeper in pairs:
            pair_frames.append({
                "ghost_value": stack[loser],
                "partner_value": stack[keeper],
                "result_value": stack[keeper] + 1,
                "ghost_from": self._col_slot(dx, insert_at + loser, size),
                "partner_pos": self._col_slot(dx, keeper, size),
                "cell_index": keeper,
            })
        drop_frames = []
        for j in range(len(final_stack)):
            old = insert_at + j
            if j != old:
                drop_frames.append({
                    "value": final_stack[j],
                    "from": self._col_slot(dx, old, size),
                    "to": self._col_slot(dx, j, size),
                })
        return {"insert_at": insert_at, "moved": moved,
                "moved_len": len(moved),
                "move_frames": move_frames, "pair_frames": pair_frames,
                "drop_frames": drop_frames}

    def _row_layers_for(self, before_cols, after_cols, base_t0):
        """Сколько слоёв реально добавил движок (по высоте стопок) — и по
        одному параллельному анимационному слою на каждый из них."""
        h_after = max((len(c) for c in after_cols.values()), default=0)
        h_before = max((len(c) for c in before_cols.values()), default=0)
        R = max(h_after - h_before, 0)
        if R <= 0:
            return []
        size = self.game.size
        layers = []
        t0 = base_t0
        for r in range(R):
            cards = []
            for x in sorted(after_cols):
                col = after_cols[x]
                i = h_after - R + r            # ранг сверху среди новых
                if i < len(col):
                    cards.append({
                        "value": col[i],
                        "start": self._col_slot(x, i, size),
                        "end": self._col_slot(x, i + R, size),
                    })
            layers.append({"t0": t0, "rows": R, "cards": cards})
            t0 += 70                           # каждый слой стартует позже
        return layers

    # --- проигрывание цепочки -------------------------------------------
    def _play_state(self, snap, game_after, pre=None, *, row_base=None,
                    deal=None, after_cb=None):
        """Ставит keyframe-цепочку и запускает таймер.

        snap       — состояние ДО (None для раздачи);
        game_after — итоговое состояние ПОСЛЕ (его рисуем по окончании);
        pre        — ключевые кадры переноса (None => только слои строк);
        deal       — число раздаваемых стартовых строк;
        after_cb   — колбэк по завершении всей цепочки.
        """
        self._base_vals = dict(snap["field"]) if snap is not None else {}
        self._pop_at = {}
        plan = []
        T = 0
        if deal is not None:
            self._deal_t0 = 0
            T = DEAL_MS + max(deal - 1, 0) * 80
        size = self.game.size
        if pre is not None:
            move_dur = MOVE_MS + max(len(pre["move_frames"]) - 1, 0) * STAGGER_MS
            mv_data = (pre["move_frames"], pre["insert_at"], pre["moved_len"])
            plan.append((0, move_dur, "move", mv_data))
            t_pair = int(move_dur * 0.6)
            for pf in pre["pair_frames"]:
                plan.append((t_pair, POP_MS, "pair", [pf]))
                # после схлопывания партнёр занимает ранг keeper // 2
                keeper = pf["cell_index"]
                rank_after = keeper // 2
                col = self._col_of(pf["partner_pos"])
                self._pop_at[(col, rank_after)] = t_pair + int(POP_MS * 0.7)
            t_drop = int(move_dur * 0.75)
            for df in pre["drop_frames"]:
                plan.append((t_drop, DROP_MS, "drop", [df]))
            T = max(T, move_dur + POP_MS, t_drop + DROP_MS)
            row_base = move_dur
        elif row_base is None:
            row_base = 0
        after_cols = self._state_of(game_after)["cols"]
        before_cols = snap["cols"] if snap is not None \
            else {x: [] for x in range(size[0])}
        layers = self._row_layers_for(before_cols, after_cols, row_base)
        for L in layers:
            dur = ROW_MS + L["rows"] * 40
            plan.append((L["t0"], dur, "row", [L]))
            T = max(T, L["t0"] + dur)
        self._plan = plan
        self._after_cb = after_cb
        if not plan:
            self._finish_anim()
            return
        self._animating = True
        self._t = 0
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    @staticmethod
    def _col_of(pt: QPointF):
        return int(round((pt.x() - PAD - CELL / 2) / (CELL + GAP)))

    def play_new_row(self, before_snap, after_game, after_cb=None):
        """Анимация «+ Строка»: КАЖДЫЙ добавленный слой въезжает сверху."""
        self._play_state(before_snap, after_game, pre=None, row_base=0,
                         after_cb=after_cb)

    def play_deal(self, rows):
        """Раздача стартовых строк при новой игре."""
        self._play_state(None, self.game, pre=None, deal=rows)

    def finish_now(self):
        """Мгновенно свернуть текущую цепочку (undo/рестарт/смена игры)."""
        self._animating = False
        self._timer.stop()
        self._plan = []
        self._deal_t0 = None
        self._pop_at = {}
        self._base_vals = {}
        self._queue = []
        self._after_cb = None
        self.update()

    def _finish_anim(self):
        was = self._animating
        cb, self._after_cb = self._after_cb, None
        self._animating = False
        self._timer.stop()
        self._plan = []
        self._deal_t0 = None
        self._pop_at = {}
        self._base_vals = {}
        if cb:
            cb()
        self.update()
        if was:
            self._drain_queue()

    def _on_tick(self):
        self._t += 16
        alive = self._deal_t0 is not None
        for (t0, dur, kind, data) in self._plan:
            if self._t < t0 + dur:
                alive = True
        if not alive:
            self._finish_anim()
        else:
            self.update()

    def _prog(self, t0, dur):
        return ease_out((self._t - t0) / float(dur))

    def _pre_top(self, x):
        """Верхний api-индекс колонки x в состоянии ДО хода (по снимку)."""
        field = self._base_vals
        h = 0
        while field.get((x, h)):
            h += 1
        size = self.game.size
        return max(size[1] - h, 0)

    def _layer_offset(self):
        """Общий вертикальный сдвиг существующих карт из-за въезжающих
        слоёв (поле едет вниз, новые строки занимают верх)."""
        off = 0.0
        for (t0, dur, kind, Ls) in self._plan:
            if kind != "row":
                continue
            for L in Ls:
                p = self._prog(t0, dur)
                off += (1.0 - p) * L["rows"] * (CELL + GAP)
        return off

    # --- события мыши --------------------------------------------------
    def _cell_at(self, pos):
        """(col, api_y) клетки под курсором или (None, None)."""
        x = self._col_at(QPointF(pos.x(), PAD + 1))
        if x is None:
            return None, None
        row = int((pos.y() - PAD) // (CELL + GAP))
        if 0 <= row <= self.game.size[1] - 1:
            return x, row
        return None, None

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        x, y = self._cell_at(ev.pos())
        if x is None:
            self.selected = None
            self.drag_from = None
            self.drag_pos = None
            self.update()
            return
        vis = self.visible_cards(x)
        prev = self.selected          # выбор ДО этого нажатия
        if not vis:
            # клик по пустой колонке: если есть выбранный источник — переносим
            if prev is not None:
                self.try_move(prev[0], prev[1], x, self.selected_count())
            else:
                self.selected = None
                self.update()
            return
        if ev.modifiers() & Qt.ShiftModifier:
            # Shift+ЛКМ: выбрать ВЕСЬ столбец (верх среза = верхняя карта)
            if prev is not None and prev[0] != x:
                self.try_move(prev[0], prev[1], x, self.selected_count())
                return
            self.selected = (x, vis[0])
            self.drag_all_column = True
            self.drag_from = (x, vis[0])
            self.drag_pos = QPointF(ev.pos())
            self._announce_selection()
            self.update()
            return
        if prev is not None and prev[0] != x:
            # второй клик по ДРУГОЙ колонке = ход «источник -> эта колонка».
            # куда именно целимся не важно: движок всегда кладёт пачку
            # под нижнюю карту целевой колонки.
            self.try_move(prev[0], prev[1], x, self.selected_count())
            return
        # выбираем конкретную карту — переносится она и ВСЁ под ней («срез»).
        # если попали в пустую клетку занятой колонки — берём ближайшую снизу
        sy = y if self.value_at(x, y) else None
        if sy is None:
            below = [v for v in vis if v >= y]
            sy = below[0] if below else vis[-1]
        if prev is not None and prev[0] == x:
            # повторный клик по ТОЙ ЖЕ колонке-источнику НЕ должен случайно
            # превратиться в ход «в себя» — только меняем/снимаем выбор
            if prev == (x, sy):
                self.selected = None
                self.drag_from = None
                self.drag_pos = None
            else:
                self.selected = (x, sy)
                self.drag_all_column = False
                self.drag_from = (x, sy)
                self.drag_pos = QPointF(ev.pos())
                self._announce_selection()
            self.update()
            return
        self.selected = (x, sy)
        self.drag_all_column = False
        self.drag_from = (x, sy)
        self.drag_pos = QPointF(ev.pos())
        self._announce_selection()
        self.update()

    def _announce_selection(self):
        win = self.window()
        if win is None or self.selected is None or win.is_game_over():
            return
        x, y = self.selected
        h = self.stack_height(x)
        cnt = self.slice_count(x, y)
        if cnt >= h:
            win.show_status(f"Выбрана колонка {x + 1}: весь столбец "
                            f"({cnt} карт). Клик по другой колонке — перенести.")
        else:
            win.show_status(f"Выбран срез колонки {x + 1}: {cnt} НИЖНИХ карт "
                            f"из {h}. ↑/↓ или колесо мыши — менять границу "
                            "среда, клик по другой колонке — перенести.")

    def mouseMoveEvent(self, ev):
        self.hover_col = self._col_at(QPointF(ev.pos().x(), PAD + 1))
        if self.drag_from is not None and (ev.buttons() & Qt.LeftButton):
            self.drag_pos = QPointF(ev.pos())
        elif not (ev.buttons() & Qt.LeftButton):
            self.drag_from = None
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        start, frm = self.drag_pos, self.drag_from
        self.drag_from = None
        self.drag_pos = None
        if frm is None or start is None:
            self.update()
            return
        dx, dy = ev.pos().x() - start.x(), ev.pos().y() - start.y()
        if abs(dx) < 12 and abs(dy) < 12:
            self.update()             # обычный клик — уже обработан в pressEvent
            return
        # drag-and-drop: бросок выбранной пачки в другую колонку
        tx = self._col_at(QPointF(ev.pos().x(), PAD + 1))
        if tx is None or tx == frm[0]:
            self.update()
            return
        cnt = self.column_height(frm[0]) if self.drag_all_column \
            else self.selected_count()
        self.try_move(frm[0], frm[1], tx, cnt)

    def wheelEvent(self, ev):
        """Колесо мыши — двигать верхнюю границу выбранного «среза» по стопке."""
        if self.selected is None:
            return
        x, y = self.selected
        vis = self.visible_cards(x)
        if not vis:
            self.selected = None
            self.update()
            return
        dy = -1 if ev.angleDelta().y() > 0 else 1
        ny = max(vis[0], min(vis[-1], y + dy))
        if ny != y:
            self.selected = (x, ny)
            self.drag_from = (x, ny)
            self._announce_selection()
            self.update()

    def leaveEvent(self, ev):
        self.hover_col = None
        self.drag_from = None
        self.update()

    def keyPressEvent(self, ev):
        key = ev.key()
        if key in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            # сдвиг границы «среза» по стопке: Up — выше (больше карт),
            # Down — ниже (меньше карт, только хвост колонки)
            x, y = self.selected
            vis = self.visible_cards(x)
            if not vis:
                self.selected = None
                self.update()
                return
            dy = -1 if key == Qt.Key_Up else 1
            ny = max(vis[0], min(vis[-1], y + dy))
            if ny != y:
                self.selected = (x, ny)
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
                    if self.visible_cards(cur):
                        break
                if self.visible_cards(cur):
                    self.selected = (cur, self.visible_cards(cur)[-1])
                    self._announce_selection()
            else:
                sx, sy = self.selected
                self.try_move(sx, sy, (sx + dx) % cols, self.slice_count(sx, sy))
            self.update()
        elif key == Qt.Key_Escape:
            self.selected = None
            self.drag_from = None
            self.drag_pos = None
            self.update()

    # --- отрисовка -----------------------------------------------------
    def _frames_of(self, kind):
        return [(t0, dur, data) for (t0, dur, k, data) in self._plan if k == kind]

    def _move_active(self):
        return bool(self._animating and self._frames_of("move"))

    @staticmethod
    def _rank_of(pt: QPointF):
        """Ранг сверху в колонке по экранному центру карты (может быть > rows)."""
        return int(round((pt.y() - PAD - CELL / 2) / (CELL + GAP)))

    def _move_meta(self):
        """(frames, insert_at, moved_len) активного move-слоя или None."""
        fr = self._frames_of("move")
        if not fr:
            return None
        data = fr[0][2]
        if isinstance(data, tuple):
            return data
        return data, 0, len(data)      # совместимость на всякий случай

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

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

        size = self.game.size
        sel_x = self.selected[0] if self.selected else None
        sel_y = self.selected[1] if self.selected else None
        anim_move = self._move_active()
        dst_x = src_x = None
        slice_top = 0
        ins = 0
        n_moved = 0
        if anim_move:
            frames, ins, n_moved = self._move_meta()
            dst_x = self._col_of(frames[0]["end"])
            src_x = self._col_of(frames[0]["start"])
            # ранг ВЕРХНЕЙ летящей карты среди статичных карт источника.
            # после хода в источнике остаётся (top_before + rank) карта;
            # верхняя летящая имела до хода api_y = sy — рисуем пропущенными
            # все клетки с api_y >= sy, т.е. rank >= sy - top_now.
            sy = self._rank_of(frames[0]["start"]) + self._pre_top(src_x)
            slice_top = max(sy - (self.top_index(src_x) or 0), 0)

        # подсветки колонок
        for x in range(size[0]):
            r = self.col_rect(x).adjusted(2, 2, -2, -2)
            if x == sel_x and not anim_move:
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

        bottom_row = size[1] - 1
        danger = any(self.game.field[x][bottom_row].value
                     for x in range(size[0]))

        # ------- клетки цели, которые рисуют pair/drop-слои --------------
        skip_cells = set()
        if anim_move:
            for (_t0, _d, pf_list) in self._frames_of("pair"):
                for pf in pf_list:
                    keeper = self._rank_of(pf["partner_pos"])
                    skip_cells.add((dst_x, ins + keeper))
            for (_t0, _d, df_list) in self._frames_of("drop"):
                for df in df_list:
                    y_to = self._rank_of(df["to"])
                    skip_cells.add((dst_x, ins + y_to))

        layer_off = self._layer_offset() if self._animating else 0.0

        # ------- статичные карты ---------------------------------------
        for x in range(size[0]):
            t = self.top_index(x)
            if t is None:
                continue
            b = self.bottom_index(x)
            n = b - t + 1
            for rank, y in enumerate(range(t, b + 1)):
                v = self.game.field[x][y].value
                rect = self.cell_rect(x, y)
                rect.translate(0, layer_off)
                if self._deal_t0 is not None:
                    tt = (self._t - rank * 80) / float(DEAL_MS)
                    if tt <= 0:
                        continue               # карта ещё за кулисами
                    rect.translate(0, (1.0 - ease_out(min(tt, 1.0)))
                                   * -(rank + 2) * (CELL + GAP))
                scale = 1.0
                pop_t = self._pop_at.get((x, rank))
                if pop_t is not None and self._t >= pop_t:
                    pp = min((self._t - pop_t) / float(POP_MS), 1.0)
                    scale = 1.0 + 0.18 * math.sin(math.pi * ease_out(pp))
                if self.selected and not anim_move and x == sel_x and y >= sel_y:
                    scale = max(scale, 1.0 + 0.03 * (rank + 1) / max(n, 1))
                dimmed = bool(self.selected) and not anim_move \
                    and not (x == sel_x and y >= sel_y)
                skip = False
                if anim_move:
                    if (x, y) in skip_cells:
                        skip = True            # летит в pair/drop-слоях
                    if x == src_x and rank >= slice_top:
                        skip = True            # летит в move-слое
                if not skip:
                    self._draw_card(p, rect, v, scale=scale, dim=dimmed,
                                    ring=(not anim_move and x == sel_x
                                          and y == sel_y))

        # ------- keyframe-слои анимации -------------------------------
        p.pushClipRegion(self.region())
        p.setClipRect(field_rect)
        for (t0, dur, mv_data) in self._frames_of("move"):
            fr_list = mv_data[0] if isinstance(mv_data, tuple) else mv_data
            for fdata in fr_list:
                local = (self._t - t0 - fdata["delay"]) / float(MOVE_MS)
                if local < 0:
                    continue                   # ещё не стартовала
                pr = ease_out(min(local, 1.0))
                x0, y0 = fdata["start"].x(), fdata["start"].y()
                x1, y1 = fdata["end"].x(), fdata["end"].y()
                cx = x0 + (x1 - x0) * pr
                cy = y0 + (y1 - y0) * pr - fdata["arc"] * math.sin(math.pi * pr)
                rect = QRectF(cx - CELL / 2, cy - CELL / 2, CELL, CELL)
                self._draw_card(p, rect, fdata["value"], alpha=235)
        for (t0, dur, pf_list) in self._frames_of("pair"):
            for pf in pf_list:
                pr = self._prog(t0, dur)
                pos = QPointF(pf["ghost_from"])
                pos.setX(pos.x() + (pf["partner_pos"].x() - pos.x()) * pr)
                pos.setY(pos.y() + (pf["partner_pos"].y() - pos.y()) * pr)
                rect = QRectF(pos.x() - CELL / 2, pos.y() - CELL / 2,
                              CELL, CELL)
                al = int(235 * (1.0 - pr ** 1.5))
                if al > 4:
                    self._draw_card(p, rect, pf["ghost_value"],
                                    scale=1.0 - 0.25 * pr, alpha=al)
                if pr > 0.7:
                    rp = pf["partner_pos"]
                    rect2 = QRectF(rp.x() - CELL / 2, rp.y() - CELL / 2,
                                   CELL, CELL)
                    q = ease_out((pr - 0.7) / 0.3)
                    self._draw_card(p, rect2, pf["result_value"],
                                    scale=1.0 + 0.15 * q,
                                    flash=int(120 * (1.0 - q)))
        for (t0, dur, df_list) in self._frames_of("drop"):
            for df in df_list:
                pr = self._prog(t0, dur)
                pos = QPointF(df["from"])
                pos.setY(pos.y() + (df["to"].y() - pos.y()) * pr)
                rect = QRectF(pos.x() - CELL / 2, pos.y() - CELL / 2,
                              CELL, CELL)
                self._draw_card(p, rect, df["value"])
        for (t0, dur, Ls) in self._frames_of("row"):
            for L in Ls:
                for cdata in L["cards"]:
                    pr = self._prog(t0, dur)
                    pos = QPointF(cdata["start"])
                    pos.setY(pos.y() + (cdata["end"].y() - pos.y()) * pr)
                    rect = QRectF(pos.x() - CELL / 2, pos.y() - CELL / 2,
                                  CELL, CELL)
                    self._draw_card(p, rect, cdata["value"],
                                    alpha=int(255 * min(1.0, pr * 2 + 0.35)))
        p.setClipping(False)
        p.pop()

        if danger:
            pen = QPen(QColor(239, 93, 93, 220))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            line_y = PAD + size[1] * (CELL + GAP) - GAP + 4
            p.drawLine(int(PAD), int(line_y),
                       int(self.board_px_w() - PAD), int(line_y))

        # ------- призрак перетаскиваемой пачки под курсором ------------
        if self.drag_from is not None and self.drag_pos is not None \
                and not anim_move:
            gx, gy = self.drag_pos.x(), self.drag_pos.y()
            g0 = self.cell_center(*self.drag_from)
            if abs(gx - g0.x()) > 12 or abs(gy - g0.y()) > 12:
                cnt = self.column_height(self.drag_from[0]) \
                    if self.drag_all_column else self.selected_count()
                x, y = self.drag_from
                vals = []
                yy = y
                while self.value_at(x, yy) and len(vals) < cnt:
                    vals.append(self.value_at(x, yy))
                    yy += 1
                for idx, v in enumerate(vals):
                    rect = QRectF(gx - CELL / 2,
                                  gy - CELL / 2 + idx * (CELL + GAP) * 0.35,
                                  CELL, CELL)
                    self._draw_card(p, rect, v, alpha=200)

        p.end()

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

        # тень
        p.setPen(Qt.NoPen)
        shadow = QRectF(r).translated(0, 3)
        p.setBrush(self._with_alpha(QColor(0, 0, 0), int(70 * alpha / 255)))
        p.drawRoundedRect(shadow, 14, 14)

        p.setBrush(lg)
        p.drawRoundedRect(r, 14, 14)

        # блик
        path = QPainterPath()
        path.addRoundedRect(QRectF(r.x(), r.y(), r.width(), r.height() * 0.45), 14, 14)
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

        # метка уровня
        f2 = QFont("Consolas", 8)
        p.setFont(f2)
        lc = QColor(text).lighter(140) if QColor(text).value() < 160 else QColor(text).darker(140)
        lc.setAlpha(alpha)
        p.setPen(lc)
        p.drawText(r.adjusted(6, 2, -6, -2), Qt.AlignTop | Qt.AlignLeft, f"L{value}")

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
        self._undo_stack = []   # список сериализованных состояний игры

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(10)

        # ------- верхняя панель -------
        header = QHBoxLayout()
        title = QLabel("MERGE CARDS")
        title.setStyleSheet(
            "color:#eaf0ff; font: bold 22px 'Segoe UI'; letter-spacing:2px;")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        # ------- статистика -------
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.lbl_points = self._stat_box(stats, "Очки", "#4a6cf7")
        self.lbl_best = self._stat_box(stats, "Лучшее комбо", "#2fbf8f")
        self.lbl_max = self._stat_box(stats, "Макс. карта", "#f0a13a")
        root.addLayout(stats)

        # ------- поле -------
        self.game, _rows = self._fresh_game()
        self.board = BoardWidget(self.game)
        root.addWidget(self.board, 0, Qt.AlignHCenter)

        # ------- статус -------
        self.lbl_status = QLabel()
        self.lbl_status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.lbl_status)

        # ------- кнопки -------
        btns = QHBoxLayout()
        btns.addStretch(1)
        b_add = QPushButton("+ Строка")
        b_undo = QPushButton("Отменить (Z)")
        b_new = QPushButton("Новая игра")
        for b in (b_add, b_undo, b_new):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(130)
            b.setStyleSheet(
                "QPushButton { background:#232c47; color:#dfe7fa; border:1px solid #34406a;"
                " border-radius:10px; padding:8px 14px; font:600 13px 'Segoe UI'; }"
                "QPushButton:hover { background:#2d3860; }"
                "QPushButton:pressed { background:#1b2338; }")
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)
        b_add.clicked.connect(self.add_row_clicked)
        b_undo.clicked.connect(self.undo_last)
        b_new.clicked.connect(self.new_game)

        self.refresh_stats()
        self.show_status("Клик по карте — выбрать (переносится она и всё под ней). "
                         "Стрелки ←→ — перенести в соседнюю колонку, ↑↓ — выбрать карту "
                         "ниже/выше в стопке. Space — новая строка, Z — отмена.")

    # вспомогательное для блоков статистики
    def _stat_box(self, layout: QHBoxLayout, name: str, accent: str) -> QLabel:
        box = QFrame()
        box.setStyleSheet(
            f"QFrame {{ background:#161d31; border:1px solid #26304e; border-radius:12px; }}"
            f"QLabel {{ color:#8f9cbf; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(2)
        cap = QLabel(name.upper())
        cap.setStyleSheet("color:#77839f; font: bold 10px 'Segoe UI'; letter-spacing:1px;")
        val = QLabel("0")
        val.setStyleSheet(f"color:{accent}; font: bold 20px 'Segoe UI';")
        lay.addWidget(cap)
        lay.addWidget(val)
        layout.addWidget(box)
        return val

    # ------- игра / undo -------
    def _fresh_game(self):
        """Новая партия: (игра, число реально добавленных движком слоёв)."""
        g = Game((BOARD_COLS, BOARD_ROWS), {})
        g.add_row()
        rows = max((sum(1 for y in range(g.size_calc[1])
                        if g.field[x][y].value)
                    for x in range(g.size[0])), default=0)
        return g, rows

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
        """Независимый клон (Game.copy() делит _bag по ссылке — это баг API)."""
        return self._deserialize(self._serialize(g))

    def push_undo(self):
        self._undo_stack.append(self._serialize(self.game))
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def pop_undo_discard(self):
        if self._undo_stack:
            self._undo_stack.pop()

    def undo_last(self, reason: str = ""):
        if not self._undo_stack:
            if reason:
                self.show_status(reason, warn=True)
            return
        st = self._undo_stack.pop()
        self.game = self._deserialize(st)
        self.board.game = self.game
        self.board.selected = None
        self.board.finish_now()
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
        if self.game_over:
            return
        # Исход доброса строки (движок внутри может добавить несколько строк
        # и объявить Game Over) предсказываем прогоном на независимой копии;
        # оригинал меняем только если копия вернула чистый успех.
        try:
            probe = self.clone_game(self.game)
            res = probe.add_row()
        except Exception:
            self.on_game_over("поле переполнено — новую строку добавить некуда")
            self.refresh_stats()
            return
        bad = (isinstance(res, tuple) or
               (res is not None and str(res).startswith("Game Over")) or
               any(probe.field[x][probe.size[1]].value
                   for x in range(probe.size[0])))
        before_snap = self.board._snapshot()
        if bad:
            # Как в голом движке: показываем финальное состояние и
            # объявляем поражение — но с анимацией въезда новых слоёв.
            self.push_undo()
            self.game = probe
            self.board.game = self.game
            self.board.selected = None
            self.refresh_stats()
            self.board.play_new_row(
                before_snap, probe,
                after_cb=lambda r=str(res or "строка заполнила поле"):
                self.on_game_over(r))
            return
        self.push_undo()
        self.game = probe
        self.board.game = self.game
        self.board.selected = None
        self.show_status("Добавлена новая строка сверху.")
        self.refresh_stats()
        self.board.play_new_row(before_snap, probe)
        self.check_deadlock()

    def board_game_changed(self, game: Game):
        """BoardWidget подменил игру (например, после подтверждённого
        проигрышного хода) — синхронизируем ссылку."""
        self.game = game
        self.board.game = game

    def on_game_over(self, reason: str):
        self.game_over = True
        self.show_status(f"Игра окончена ({reason}). «Отменить» (Z) вернёт последний ход, "
                         "«Новая игра» или R — начнёт заново.", warn=True)

    def is_game_over(self):
        return self.game_over

    def check_deadlock(self):
        """Game Over, когда не осталось ни одного хода «Success» и «+ Строка»
        тоже ведёт к заполнению поля. Ходы, которыми движок заранее объявляет
        Game Over, поражением «автоматом» не считаются — они требуют
        подтверждения игроком (см. BoardWidget.try_move)."""
        if self.game_over:
            return
        if not self.board.has_any_action():
            self.on_game_over("не осталось ни ходов, ни места для новой строки")

    def new_game(self):
        self.game, rows = self._fresh_game()
        self.board.game = self.game
        self.board.selected = None
        self.board.finish_now()
        self.board.setMinimumSize(self.board.board_px_w(), self.board.board_px_h())
        self._undo_stack.clear()
        self.game_over = False
        self.refresh_stats()
        self.show_status("Новая игра началась!")
        self.board.play_deal(rows)

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
