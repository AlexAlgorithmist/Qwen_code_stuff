--- main.py (原始)
# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) для игры "mergeCards" на PyQt5.

Игровая логика — mergeCards/API.py (класс Game). GUI использует движок
как есть, но честно сообщает игроку исход каждого хода до его совершения.

Как устроен движок (важно для понимания правил и анимаций):
    - y = 0 — верхний ряд поля; новые строки появляются СВЕРХУ и сдвигают
      все карты вниз; карты, упавшие за нижний край (y >= size[1]), — смерть;
    - перенос забирает выбранную карту и ВСЁ, что ниже неё в колонке, и
      кладёт пачку СНИЗУ (под нижнюю карту) колонки-цели;
    - после переноса, если слияний не было, движок сам добавляет новую
      строку; если после этого нет ни одной пары одинаковых карт на всём
      поле — он продолжает добавлять строки, пока поле не умрёт.
      Поэтому «бессмысленные» ходы реально могут кончиться прямо в Game Over.

Управление мышью (перенести МОЖНО любой «срез» колонки — карту и всё под
ней, а не обязательно весь столбец):
    - клик по карте             — выбрать именно эту карту (верх среза);
    - drag (зажать и тащить)   — перенести выбранный срез одним жестом:
                                  тянуть можно за ЛЮБУЮ карту среза, граница
                                  среда при этом не меняется;
    - клик по другой колонке    — перенести выбранный срез туда;
    - ↑ / ↓ или колесо мыши     — двигать верхнюю границу среза по стопке;
    - Shift+клик                — выбрать ВЕСЬ столбец;
    - ← / →                     — с клавиатуры: перенести срез в соседнюю
      колонку (если выбор уже сделан), иначе — выбрать колонку;
    - Пробел                    — добавить строку сверху (add_row);
    - Z                         — отменить последний ход;
    - R                         — новая игра;  Esc — выход.

Анимации (движок мутирует поле мгновенно, поэтому GUI сравнивает снимок
дохода «до/после» и проигрывает результат как последовательность фаз):
    1. полёт переносимых карт из источника в цель (по дуге, со стагом);
    2. схлопывание пар (карты сливаются в одну с pop-эффектом и вспышкой);
    3. каскадный въезд НОВЫХ строк сверху (анимируются ВСЕ добавленные
       строки, а не одна; при game over — включая умершие за краем);
    4. оседание старых карт вниз под новыми строками.
Во время анимации поле заблокировано (~600 мс), история кликов сохраняется,
undo работает корректно (откат мгновенный).

Честный проигрыш: исход каждого хода предсказывается честным прогоном на
независимой копии движка (с независимым мешком _bag — у Game.copy() баг:
_bag передаётся по ссылке). Если копия вернула «Game Over...», GUI применяет
этот финальный ход к игре и объявляет поражение — ровно так, как это делает
голый движок, без предупреждений-подтверждений. Игра завершается также,
когда не осталось ни одного принятого движком хода и «+ Строка» убивает поле.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mergeCards"))

from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer  # noqa: E402
from PyQt5.QtGui import (  # noqa: E402
    QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPen,
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


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_inout(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Планировщик анимаций: превращает снимки «до/после» в таймлайн фаз
# ---------------------------------------------------------------------------

class MovePlanner:
    """Строит план анимации хода, воспроизводя механику движка шаг за шагом.

    Движок (mergeCards/API.Game) мутирует поле мгновенно, поэтому GUI
    проигрывает переход «до/после» как осмысленную последовательность:
      fly    — полёт перенесённой пачки из источника в колонку-цель;
      vanish — схлопывание карт, слившихся в пару (или канувших под край);
      pop    — рождение результата слияния (pop + вспышка);
      slide  — оседание колонок вверх к y=0 после слияний и съезд старых
               карт вниз при появлении новых строк;
      spawn  — въезд СВЕРХУ всех добавленных движком строк (каскадом).

    Для переносов plan() сначала пытается точно симулировать ход движка
    (simulate_move): перенос -> merge/compress цели -> доброс k строк,
    подбирая вырез среза и k так, чтобы результат совпал со снимком after.
    Если точная симуляция не сходится, используется эвристическое
    сопоставление снимков (_diff_steps) — оно же применяется для add_row.
    """

    @staticmethod
    def _cols(snap):
        """{(x,y):v} -> {x: {y:v}} только занятые клетки."""
        out = {x: {} for x in range(BOARD_COLS)}
        for (x, y), v in snap.items():
            if v:
                out[x][y] = v
        return out

    @staticmethod
    def _clean(col):
        return {y: v for y, v in col.items() if v}

    # =====================================================================
    # Основной вход
    # =====================================================================
    @staticmethod
    def plan(before: dict, after: dict, src_x=None, dst_x=None):
        steps = None
        if src_x is not None and dst_x is not None:
            try:
                steps = MovePlanner.simulate_move(before, src_x, dst_x, after)
            except Exception:
                steps = None
        if steps is None:
            steps = MovePlanner._diff_steps(before, after)
        return MovePlanner.timeline(steps)

    # =====================================================================
    # Точная симуляция хода по правилам движка (без случайного мешка):
    #   1. срез src[cut:] кладётся ПОД нижнюю карту dst подряд;
    #   2. merge+compress колонки dst (пара -> верхняя карта +1, нижняя
    #      исчезает, затем колонка оседает к 0);
    #   3. если слияний не было — движок добавил k>=1 строк сверху ВСЕХ
    #      колонок (содержимое берём из after), затем снова merge всего
    #      поля (цепочки от новой строки тоже описываются).
    # Возвращает протокол шагов или None, если раскладка не сходится.
    # =====================================================================
    @staticmethod
    def simulate_move(before: dict, src_x: int, dst_x: int, after: dict):
        cb, ca = MovePlanner._cols(before), MovePlanner._cols(after)
        src_cells = sorted(cb[src_x])
        if not src_cells:
            return None

        others_same = all(MovePlanner._clean(cb[x]) == MovePlanner._clean(ca[x])
                          for x in range(BOARD_COLS)
                          if x not in (src_x, dst_x))

        for cut in range(len(src_cells)):
            pack = [cb[src_x][y] for y in src_cells[cut:]]
            steps = []
            # --- 1. перенос ---
            frm_cells = [(src_x, y) for y in src_cells[cut:]]
            dst_rows = sorted(cb[dst_x])
            base_y = (max(dst_rows) + 1) if dst_rows else 0
            col_dst = dict(cb[dst_x])
            for i, v in enumerate(pack):
                yy = base_y + i
                col_dst[yy] = v
                steps.append({'op': 'fly', 'frm': frm_cells[i],
                              'to': (dst_x, yy), 'v': v})
            src_col = {y: v for y, v in cb[src_x].items() if y < src_cells[cut]}

            # --- 2. merge+compress колонки цели ---
            merged_any, col_dst, msteps = MovePlanner._merge_col(dst_x, dict(col_dst))
            steps += msteps

            if not others_same:
                continue

            if not merged_any:
                res = MovePlanner._try_growth(src_x, dst_x, src_col, col_dst,
                                              cb, ca, steps)
                if res is not None:
                    return res
                continue

            if MovePlanner._clean(col_dst) == MovePlanner._clean(ca[dst_x]) and \
               MovePlanner._clean(src_col) == MovePlanner._clean(ca[src_x]):
                return steps
            res = MovePlanner._try_growth(src_x, dst_x, src_col, col_dst,
                                          cb, ca, steps)
            if res is not None:
                return res
        return None

    @staticmethod
    def _merge_col(x, col):
        """Алгоритм Game.merge/_merge/_compress для одной колонки: пары
        схлопываются (результат — ВЕРХНЯЯ клетка пары), затем колонка
        оседает к 0. Возвращает (was_merged, new_col, steps)."""
        steps = []
        merged_any = False
        changed = True
        while changed:
            changed = False
            ys = sorted(MovePlanner._clean(col))
            for a, b in zip(ys, ys[1:]):
                if col[a] == col[b]:
                    nv = col[a] + 1
                    steps.append({'op': 'vanish', 'at': (x, b), 'v': col[b]})
                    steps.append({'op': 'merge', 'at': (x, a), 'v': nv})
                    col[a] = nv
                    col[b] = 0
                    merged_any = changed = True
                    break
        ys_all = sorted(MovePlanner._clean(col))
        newcol = {}
        for new_y, old_y in enumerate(ys_all):
            v = col[old_y]
            if new_y != old_y:
                steps.append({'op': 'slide', 'frm': (x, old_y),
                              'to': (x, new_y), 'v': v})
            newcol[new_y] = v
        return merged_any, newcol, steps

    @staticmethod
    def _try_growth(src_x, dst_x, src_col, col_dst, cb, ca, steps_in):
        """Подбирает число добавленных строк k: состояние до доброса со
        сдвигом на k должно совпасть с after, а оставшиеся клетки after
        (rows < k) — это новые строки. Описывает cascade-down, spawn и
        пост-добросовые слияния. Возвращает шаги или None."""
        cur = {}
        for x in range(BOARD_COLS):
            if x == src_x:
                cur[x] = MovePlanner._clean(src_col)
            elif x == dst_x:
                cur[x] = MovePlanner._clean(col_dst)
            else:
                cur[x] = MovePlanner._clean(cb[x])

        for k in range(1, BOARD_ROWS + 8):
            ok = True
            used_after = set()
            for x in range(BOARD_COLS):
                for y, v in cur[x].items():
                    ny = y + k
                    if ca[x].get(ny) != v:
                        ok = False
                        break
                    used_after.add((x, ny))
                if not ok:
                    break
            if not ok:
                continue
            leftover = {}
            bad = False
            for x in range(BOARD_COLS):
                for y, v in ca[x].items():
                    if (x, y) not in used_after:
                        if y >= k:
                            bad = True
                            break
                        leftover.setdefault(x, {})[y] = v
                if bad:
                    break
            if bad:
                continue

            steps = list(steps_in)
            grown = {x: {y + k: v for y, v in cur[x].items()}
                     for x in range(BOARD_COLS)}
            for x in range(BOARD_COLS):
                for y, v in sorted(leftover.get(x, {}).items()):
                    grown[x][y] = v

            # моделируем post-merge движка поверх grown: он обязан дать ca
            extra = []
            final_ok = True
            for x in range(BOARD_COLS):
                _mm, ncol, msteps = MovePlanner._merge_col(x, dict(grown[x]))
                if MovePlanner._clean(ncol) != MovePlanner._clean(ca[x]):
                    final_ok = False
                    break
                extra += msteps
            if not final_ok:
                continue

            # cascade: старые карты съезжают вниз на k (снизу вверх)
            for x in range(BOARD_COLS):
                for y, v in sorted(cur[x].items(), reverse=True):
                    steps.append({'op': 'slide', 'frm': (x, y),
                                  'to': (x, y + k), 'v': v})
            # spawn новых клеток rows<k; результаты post-merge делает pop,
            # а их нижние половины — vanish (уже описаны в extra)
            popped = {tuple(s['at']) for s in extra if s['op'] == 'merge'}
            for x in range(BOARD_COLS):
                for y, v in sorted(leftover.get(x, {}).items()):
                    if (x, y) in popped:
                        continue
                    steps.append({'op': 'spawn', 'at': (x, y), 'v': v})
            steps += extra
            return steps
        return None

    # =====================================================================
    # Эвристический разбор чистой разницы двух снимков (add_row и пр.)
    # =====================================================================
    @staticmethod
    def _diff_steps(before: dict, after: dict):
        cb, ca = MovePlanner._cols(before), MovePlanner._cols(after)
        steps = []
        stay = set()
        matched_after = set()
        for x in range(BOARD_COLS):
            for y, v in sorted(cb[x].items()):
                if ca[x].get(y) == v and (x, y) not in matched_after:
                    matched_after.add((x, y))
                    stay.add((x, y))

        moved_cards = [(x, y) for x in range(BOARD_COLS)
                       for y in cb[x] if (x, y) not in stay]
        by_val = {}
        for x in range(BOARD_COLS):
            for y, v in ca[x].items():
                by_val.setdefault(v, []).append((x, y))
        used_after = set(matched_after)
        slides, lost = [], []
        for fp in sorted(moved_cards, key=lambda p: (-p[1], p[0])):
            fv = cb[fp[0]][fp[1]]
            cand = None
            for tp in by_val.get(fv, []):
                if tp in used_after:
                    continue
                d = abs(tp[1] - fp[1]) + (abs(tp[0] - fp[0]) * 4
                                          if tp[0] != fp[0] else 0)
                if cand is None or d < cand[0]:
                    cand = (d, tp)
            if cand and cand[0] <= 10:
                used_after.add(cand[1])
                slides.append((fp, cand[1], fv))
            else:
                lost.append(fp)
        for f, t_, v in slides:
            steps.append({'op': 'slide', 'frm': f, 'to': t_, 'v': v})

        appeared = {}
        for x in range(BOARD_COLS):
            for y, v in ca[x].items():
                if (x, y) not in used_after:
                    appeared[(x, y)] = v
        lost_set = set(lost)
        merged_results = set()
        for ap in sorted(appeared, key=lambda p: (p[1], p[0])):
            av = appeared[ap]
            parts = []
            for nb in ((ap[0], ap[1] - 1), (ap[0], ap[1] + 1)):
                if nb in lost_set and cb[nb[0]].get(nb[1]) == av - 1:
                    parts.append(nb)
            if len(parts) == 2 or (parts and before.get(ap, 0) == 0):
                for q in parts:
                    lost_set.discard(q)
                    steps.append({'op': 'vanish', 'at': q, 'v': cb[q[0]][q[1]]})
                steps.append({'op': 'merge', 'at': ap, 'v': av})
                merged_results.add(ap)
        for p in sorted(lost_set, key=lambda c: (c[1], c[0])):
            steps.append({'op': 'vanish', 'at': p, 'v': cb[p[0]][p[1]]})
        for ap, av in sorted(appeared.items(), key=lambda it: (it[0][1], it[0][0])):
            if ap in merged_results:
                continue
            steps.append({'op': 'spawn', 'at': ap, 'v': av})
        return steps

    # =====================================================================
    # Протокол шагов -> таймлайн фаз [(dur_ms, [event...]), ...]
    # =====================================================================
    @staticmethod
    def timeline(steps):
        FLY, VAN, POP, SLD, SPW = (BoardWidget.FLY_MS, BoardWidget.VANISH_MS,
                                   BoardWidget.POP_MS, BoardWidget.SLIDE_MS,
                                   BoardWidget.SPAWN_MS)
        phases = []
        groups = []
        for st in steps:
            if groups and groups[-1][0] == st['op']:
                groups[-1][1].append(st)
            else:
                groups.append((st['op'], [st]))
        for op, items in groups:
            ev = []
            if op == 'fly':
                items.sort(key=lambda s: s['to'][1])
                t = 0.0
                for s in items:
                    ev.append({'kind': 'fly', 'frm': s['frm'], 'to': s['to'],
                               'v': s['v'], 'delay': round(t)})
                    t += 70.0
                phases.append((FLY + min(t, 350.0), ev))
            elif op == 'vanish':
                t = 0.0
                for s in sorted(items, key=lambda q: (q['at'][1], q['at'][0])):
                    ev.append({'kind': 'vanish', 'at': s['at'], 'v': s['v'],
                               'delay': round(t)})
                    t += 60.0
                phases.append((VAN + min(t, 240.0), ev))
            elif op == 'merge':
                t = 0.0
                for s in sorted(items, key=lambda q: (q['at'][1], q['at'][0])):
                    ev.append({'kind': 'pop', 'at': s['at'], 'v': s['v'],
                               'delay': round(t)})
                    t += 90.0
                phases.append((POP + min(t, 360.0), ev))
            elif op == 'slide':
                items.sort(key=lambda s: -s['frm'][1])
                t = 0.0
                for s in items:
                    ev.append({'kind': 'slide', 'frm': s['frm'], 'to': s['to'],
                               'v': s['v'], 'delay': round(t)})
                    t += 25.0
                phases.append((SLD + min(t, 250.0), ev))
            elif op == 'spawn':
                rows = sorted({s['at'][1] for s in items})
                rank = {r: i for i, r in enumerate(rows)}
                for s in sorted(items, key=lambda q: (q['at'][1], q['at'][0])):
                    ev.append({'kind': 'spawn', 'at': s['at'], 'v': s['v'],
                               'delay': round(rank[s['at'][1]] * 110.0
                                              + s['at'][0] * 12.0)})
                last_rank = max(rank.values()) if rank else 0
                phases.append((SPW + last_rank * 110.0, ev))
        if not phases:
            phases = [(1, [])]
        return phases


# ---------------------------------------------------------------------------
# Виджет игрового поля
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    """Рисует поле Game и обрабатывает клики/drag-and-drop колонок."""

    FLY_MS = 430
    VANISH_MS = 300
    POP_MS = 260
    SLIDE_MS = 300
    SPAWN_MS = 380

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected = None              # (x, y) — выбранная карта-источник
        self.hover_col = None             # колонка под курсором
        self.dragging = False             # активное перетаскивание?
        self.drag_pos = None              # позиция курсора во время перетаскивания
        self._press_cell = None           # клетка, где началось нажатие
        self._press_pos = None            # точка нажатия (локальные координаты)
        self._press_slice = None          # срез, захваченный в pressEvent для drag
        self._press_moved = False         # было ли движение после press

        self.merge_flash = {}             # (x, y) -> оставшееся время вспышки, мс
        self.row_slide = 0.0              # прогресс анимации сдвига строки (0..1)

        # состояние анимационного плеера
        self.anim_active = False
        self._timeline = []               # [(start_ms, dur_ms, ev), ...]
        self._tl_total = 0
        self._tl_elapsed = 0
        self._base = {}                   # снимок поля ДО хода (для фаз)
        self._after = {}                  # снимок ПОСЛЕ (целевое состояние)
        self._carry = {}                  # (x,y)->v: карты в полёте (скрыть в базе)
        self._ghosts = {}                 # (x,y)->v: силуэты улетающих карт
        self._flights = []                # активные полёты
        self._vanishing = []              # схлопывающиеся карты
        self._popping = []                # pop-эффекты результата слияний
        self._sliding = []                # оседания старых карт
        self._spawning = []               # въезд новых карт/строк
        self._pending_gameover = None     # сообщение о смерти показать после анимации

        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._on_anim_tick)

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
        """Центр клетки (x, y). В API y = 0 — ВЕРХНИЙ видимый ряд: именно
        туда движок добавляет новые строки (_addrow сдвигает карты вниз).
        Значит row_экрана == y_api (без инверсии)."""
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
        """Сколько карт будет перенесено при текущем выборе (срез: выбранная
        карта и всё под ней — так делает action_full)."""
        if self.selected is None:
            return 0
        x, y = self.selected
        return self.slice_count(x, y)

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
        result == 'Success' либо 'Game Over...' — оба считаются действиями
        (см. has_any_action). Перебираются только видимые карты.
        """
        out = []
        for sx in range(self.game.size[0]):
            vis = self.visible_cards(sx)
            if not vis:
                continue
            for sy in vis:
                cnt = self.slice_count(sx, sy)
                for dx in range(self.game.size[0]):
                    if dx == sx:
                        continue
                    r = self._try_move(sx, sy, dx, cnt)
                    if r is None:
                        continue
                    res, gc, added = r
                    out.append((sx, sy, dx, cnt, res))
        return out

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
    def snapshot(self, g: Game):
        """{(x, y): value} всех занятых клеток (включая скрытые за краем)."""
        out = {}
        for x in range(g.size[0]):
            for y in range(g.size_calc[1]):
                v = g.field[x][y].value
                if v:
                    out[(x, y)] = v
        return out

    def try_move(self, src_x, src_y, dst_x, count_rows=None):
        """Совершает перенос; возвращает True, если ход был принят."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return False
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
        if isinstance(res_probe, tuple):
            res_probe = str(res_probe[0])
        if res_probe == "Invalid position":
            self.selected = None
            win.show_status("Движок отвергает такой перенос "
                            "(источник пуст или позиция недопустима).",
                            warn=True)
            self.update()
            return False
        before = self.snapshot(self.game)
        if res_probe.startswith("Game Over"):
            # Голый движок на таком ходе просто возвращает "Game Over..." и
            # оставляет поле в финальном состоянии. Делаем ровно то же:
            # применяем результат прогона на копии и объявляем поражение —
            # без «подтверждений», которые раньше делали игру непроигрываемой.
            win.push_undo()
            after = self.snapshot(gc_probe)
            self.game = gc_probe
            win.board_game_changed(self.game)
            self.selected = None
            self._play(before, after, src_x, dst_x,
                       gameover_msg=res_probe)
            win.refresh_stats()
            return True
        # Ход «Success» на копии — применяем его к оригиналу. Если движок
        # при ходе НЕ добавлял новую строку, он детерминирован и результат
        # в точности совпадает с движком без оболочки; если строку добавил,
        # там есть randint — финальное состояние берём с проверенной копии,
        # чтобы экран показывал ровно тот исход, который был предсказан.
        win.push_undo()
        try:
            real_res, real_added = self.game.action_full(
                (src_x, src_y), dst_x, count=count_rows)
        except Exception:
            real_res, real_added = "Game Over: engine exception", False
        if isinstance(real_res, tuple):
            real_res = str(real_res[0])
        if real_res != "Success":
            after = self.snapshot(gc_probe)
            self.game = gc_probe
            win.board_game_changed(gc_probe)
            msg = str(real_res) if str(real_res).startswith("Game Over") \
                else res_probe
        else:
            after = self.snapshot(self.game)
            added = real_added or added_probe
            if added and added_probe:
                same = (all(self.game.field[x][y].value == gc_probe.field[x][y].value
                            for x in range(self.game.size[0])
                            for y in range(self.game.size_calc[1]))
                        and self.game.points == gc_probe.points)
                if not same:
                    self.game = gc_probe
                    win.board_game_changed(gc_probe)
                    after = self.snapshot(gc_probe)
            msg = None
        self.selected = None
        combo = max(self.game.lastCombo) if self.game.lastCombo else 0
        if msg is None:
            win.show_status(f"Комбо x{combo}!" if combo > 1 else
                            f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        self._play(before, after, src_x, dst_x, gameover_msg=msg)
        if msg is None:
            win.check_deadlock()
        return True

    def do_add_row(self):
        """«+ Строка»: прогон на независимой копии, затем анимация всего
        произошедшего (движок может добавить несколько строк сразу)."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return
        try:
            probe = self.clone_or_self()
            res = probe.add_row()
        except Exception:
            win.on_game_over("поле переполнено — новую строку добавить некуда")
            win.refresh_stats()
            return
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
        self._play(before, after, None, None, gameover_msg=msg)
        if not bad:
            win.check_deadlock()

    def clone_or_self(self) -> Game:
        win = self.window()
        if win is not None and hasattr(win, "clone_game"):
            return win.clone_game(self.game)
        return self._fallback_clone(self.game)

    # --- планирование и плеер анимаций --------------------------------
    def _play(self, before, after, src_x, dst_x, gameover_msg=None):
        """Строит таймлайн по снимкам и запускает плеер."""
        try:
            phases = MovePlanner.plan(before, after, src_x, dst_x)
        except Exception:
            phases = [(1, [])]        # план построился с ошибкой — без анимации
        timeline = []
        t = 0
        for dur, evs in phases:
            for ev in evs:
                d = ev.pop('delay', 0)
                timeline.append((t + d, dur, ev))
            t += dur
        # спец-фаза: карты, оказавшиеся за нижним краем (смерть) — въезжают
        # сверху вместе с остальными новыми, но едут чуть дальше за край
        self._timeline = timeline
        self._tl_total = max(t, 1)
        self._tl_elapsed = 0
        self._base = before
        self._after = after
        self._carry = {}
        self._ghosts = {}
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []
        self._pending_gameover = gameover_msg
        self.anim_active = True
        self._start_anim()
        self.update()

    def _start_anim(self):
        if not self._anim.isActive():
            self._anim.start()

    def _on_anim_tick(self):
        if self.anim_active:
            self._advance()
            self.update()
            if self._tl_elapsed >= self._tl_total:
                self._finish_anim()
            return
        alive = False
        for k in list(self.merge_flash):
            self.merge_flash[k] -= 16
            if self.merge_flash[k] <= 0:
                del self.merge_flash[k]
            else:
                alive = True
        if self.row_slide > 0:
            self.row_slide = max(0.0, self.row_slide - 0.08)
            alive = True
        self.update()
        if not alive:
            self._anim.stop()

    def _advance(self):
        self._tl_elapsed += 16
        el = self._tl_elapsed
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []
        self._carry = {}
        self._ghosts = {}
        for start, dur, ev in self._timeline:
            if el < start or el > start + dur:
                continue
            u = (el - start) / float(dur)
            kind = ev['kind']
            if kind == 'fly':
                self._carry[ev['frm']] = ev['v']
                self._flights.append((ev['frm'], ev['to'], ev['v'], u))
            elif kind == 'slide':
                self._sliding.append((ev['frm'], ev['to'], ev['v'], u))
            elif kind == 'vanish':
                self._vanishing.append((ev['at'], ev['v'], u))
                # под схлопывающейся картой может стоять результат будущего
                # слияния (тот же клетка в after) — рисуем только vanishing
                if ev['at'] in self._after:
                    self._carry[ev['at']] = self._after[ev['at']]
            elif kind == 'pop':
                self._popping.append((ev['at'], ev['v'], u))
            elif kind == 'spawn':
                self._spawning.append((ev['at'], ev['v'], u))

    def _finish_anim(self):
        self.anim_active = False
        self._timeline = []
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []
        self._carry = {}
        self._ghosts = {}
        # вспышки на местах слияний — короткое послесвечение
        for start, dur, ev in ():
            pass
        win = self.window()
        if self._pending_gameover is not None and win is not None:
            win.on_game_over(self._pending_gameover)
            self._pending_gameover = None
        self.update()

    # --- события мыши --------------------------------------------------
    @staticmethod
    def _ev_pos_f(ev) -> QPointF:
        """Локальная позиция курсора (QPointF) из события мыши — кроссверсионно."""
        try:
            return ev.position()          # Qt >= 5.14
        except AttributeError:
            pass
        try:
            return ev.localPos()          # PyQt5: QPointF в локальных координатах
        except AttributeError:
            return QPointF(ev.pos())

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
        if self.anim_active:
            return
        x, y = self._cell_at(ev.pos())
        self._press_cell = (x, y)
        self._press_pos = QPointF(ev.localPos() if hasattr(ev, "localPos") else ev.pos())
        self._press_moved = False
        # drag должен работать и без предварительного клика-выбора:
        # запоминаем срез, который потянем именно из этой точки
        if x is not None and self.selected is None:
            vis = self.visible_cards(x)
            sy = None
            if vis:
                sy = y if self.value_at(x, y) else \
                    (min([v for v in vis if v >= y], default=None) or vis[-1])
            self._press_slice = (x, sy) if sy is not None else None
        else:
            self._press_slice = None
        if x is None:
            self.selected = None
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
            else:
                self.selected = (x, sy)
                self._announce_selection()
            self.update()
            return
        self.selected = (x, sy)
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
                            "среза, клик по другой колонке — перенести.")

    def mouseMoveEvent(self, ev):
        self.hover_col = self._col_at(QPointF(ev.pos().x(), PAD + 1))
        if (self._press_cell is not None and (ev.buttons() & Qt.LeftButton)
                and not self.anim_active):
            px, py = self._press_cell
            if px is not None:
                cur = self._ev_pos_f(ev)
                # старт drag — по смещению ОТ ТОЧКИ НАЖАТИЯ (не от центра
                # клетки: центр может лежать вне видимой области виджета)
                if (self._press_pos is not None and
                        (abs(cur.x() - self._press_pos.x()) > 6 or
                         abs(cur.y() - self._press_pos.y()) > 6)):
                    self._press_moved = True
                    self.dragging = True
                    self.drag_pos = QPointF(cur)
        elif not (ev.buttons() & Qt.LeftButton):
            self.dragging = False
            self.drag_pos = None
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        was_drag = self.dragging and self._press_moved
        press_cell = self._press_cell
        drop_pos = self._ev_pos_f(ev)
        self.dragging = False
        self.drag_pos = None
        self._press_cell = None
        self._press_pos = None
        self._press_moved = False
        if self.anim_active:
            self.update()
            return
        if not was_drag:
            self.update()             # обычный клик — уже обработан в pressEvent
            return
        # drag-and-drop: бросок выбранного среза в другую колонку.
        # Тянуть можно за любую карту среза — важны только колонка-источник
        # (она не меняется) и колонка-цель.
        if press_cell is None or press_cell[0] is None:
            self.update()
            return
        sx = press_cell[0]
        if self.selected is None or self.selected[0] != sx:
            # начали тянуть неподготовленную колонку — берём срез,
            # вычисленный в точке захвата (pressEvent), и тут же переносим
            grab = self._press_slice if (self._press_slice and
                                         self._press_slice[0] == sx) else None
            vis = self.visible_cards(sx)
            if not vis:
                self.update()
                return
            y = grab[1] if grab else None
            if y is None or not self.value_at(sx, y):
                below = [v for v in vis if v >= (y if y is not None else vis[0])]
                y = below[0] if below else vis[-1]
            self.selected = (sx, y)
        tx = self._col_at(QPointF(drop_pos.x(), PAD + 1))
        if tx is None or tx == sx:
            self.update()             # бросок «в никуда»/в свою колонку — выбор
            return                    # остаётся активным (можно довести кликом)
        self.try_move(sx, self.selected[1], tx, self.slice_count(sx, self.selected[1]))

    def wheelEvent(self, ev):
        """Колесо мыши — двигать верхнюю границу выбранного «среза» по стопке."""
        if self.anim_active or self.selected is None:
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
            self.update()

    # --- отрисовка -----------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        try:
            self._paint_frame(p)
        except Exception:
            pass                     # кадр не должен ронять приложение
        finally:
            p.end()

    def _paint_frame(self, p):

        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, BG_TOP)
        grad.setColorAt(1, BG_BOTTOM)
        p.fillRect(self.rect(), grad)

        # фон поля
        field_rect = QRectF(PAD - 6, PAD - 6,
                            self.board_px_w() - 2 * PAD + 12,
                            self.board_px_h() - 2 * PAD + 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(field_rect, 16, 16)

        sel_x = self.selected[0] if self.selected else None
        sel_y = self.selected[1] if self.selected else None

        # подсветки колонок
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

        # danger: карты дошли до нижнего ряда — поле заполнено
        bottom_row = self.game.size[1] - 1
        danger = any(self.game.field[x][bottom_row].value
                     for x in range(self.game.size[0]))

        # ---- базовые карты ----
        if self.anim_active:
            self._paint_animated(p, sel_x, sel_y)
        else:
            for x in range(self.game.size[0]):
                t = self.top_index(x)
                if t is None:
                    continue
                b = self.bottom_index(x)
                n = b - t + 1
                for i, y in enumerate(range(t, b + 1)):
                    v = self.game.field[x][y].value
                    rect = self.cell_rect(x, y)
                    slide_off = 0.0
                    if self.row_slide > 0 and y == 0:
                        slide_off = -self.row_slide * (CELL + GAP)
                    rect.translate(0, slide_off)
                    scale = 1.0
                    if self.selected and x == sel_x and y >= sel_y:
                        scale = 1.0 + 0.03 * (i + 1) / max(n, 1)
                    dimmed = bool(self.selected) and not (x == sel_x and y >= sel_y)
                    self._draw_card(p, rect, v, scale=scale,
                                    flash=self.merge_flash.get((x, y), 0),
                                    dim=dimmed,
                                    ring=(x == sel_x and y == sel_y))

        if danger:
            pen = QPen(QColor(239, 93, 93, 220))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            line_y = PAD + self.game.size[1] * (CELL + GAP) - GAP + 4
            p.drawLine(int(PAD), int(line_y),
                       int(self.board_px_w() - PAD), int(line_y))

        # призрак перетаскиваемой пачки (реальный drag-and-drop)
        if self.dragging and self.drag_pos is not None and self.selected:
            gx, gy = self.drag_pos.x(), self.drag_pos.y()
            sx, sy = self.selected
            cnt = self.slice_count(sx, sy)
            vals = []
            yy = sy
            while self.value_at(sx, yy) and len(vals) < cnt:
                vals.append(self.value_at(sx, yy))
                yy += 1
            # карта, за которую реально держим курсор (точка захвата) —
            # чтобы стек висел ровно под курсором, а не «прыгал» к верхней
            grab_row = sy
            if self._press_slice and self._press_slice[0] == sx and                     self._press_cell and self._press_cell[1] is not None:
                py = self._press_cell[1]
                if py >= sy and self.value_at(sx, py):
                    grab_row = py
            dy0 = gy - self.cell_center(sx, grab_row).y()
            for i, v in enumerate(vals):
                rect = QRectF(gx - CELL / 2,
                              self.cell_center(sx, sy).y() + dy0 - CELL / 2
                              + i * (CELL + GAP) * 0.45,
                              CELL, CELL)
                self._draw_card(p, rect, v, alpha=200)
            # подсветка колонки-цели
            tx = self._col_at(QPointF(gx, PAD + 1))
            if tx is not None and tx != sx:
                r = self.col_rect(tx).adjusted(2, 2, -2, -2)
                p.setPen(QPen(QColor(125, 230, 92, 220), 2))
                p.setBrush(QColor(125, 230, 92, 26))
                p.drawRoundedRect(r, 14, 14)
                p.setPen(Qt.NoPen)

    def _paint_animated(self, p: QPainter, sel_x, sel_y):
        """Кадр анимированного перехода между снимками _base и _after."""
        moving_src = {f for f, _t, _v, _u in self._flights}
        sliding_frm = {f for f, _t, _v, _u in self._sliding}
        vanish_at = {a for a, _v, _u in self._vanishing}
        pop_at = {a for a, _v, _u in self._popping}
        spawn_at = {a for a, _v, _u in self._spawning}

        # статичные карты целевого состояния
        for (x, y), v in self._after.items():
            if y < 0 or y >= self.game.size[1]:
                continue
            if (x, y) in spawn_at or (x, y) in pop_at or (x, y) in moving_src \
                    or (x, y) in sliding_frm or (x, y) in vanish_at:
                continue
            if (x, y) in self._carry:
                continue
            rect = self.cell_rect(x, y)
            self._draw_card(p, rect, v)

        # оседания старых карт под новые строки
        for frm, to, v, u in self._sliding:
            e = ease_inout(u)
            c1, c2 = self.cell_center(*frm), self.cell_center(*to)
            cx, cy = c1.x(), c1.y() + (c2.y() - c1.y()) * e
            rect = QRectF(cx - CELL / 2, cy - CELL / 2, CELL, CELL)
            if rect.bottom() > PAD and rect.top() < self.board_px_h() - PAD:
                self._draw_card(p, rect, v)

        # схлопывающиеся карты (участники слияний и просто исчезающие)
        for at, v, u in self._vanishing:
            e = ease_inout(u)
            rect = self.cell_rect(*at)
            sc = 1.0 - 0.92 * e
            al = int(255 * (1.0 - e))
            if sc > 0.05 and al > 0:
                self._draw_card(p, rect, v, scale=sc, alpha=al)

        # полёты: летящая карта + её силуэт на старом месте
        for frm, to, v, u in self._flights:
            e = ease_inout(u)
            c1, c2 = self.cell_center(*frm), self.cell_center(*to)
            arc = -min(abs(c2.x() - c1.x()), 140.0) * 0.35 * \
                (4.0 * e * (1.0 - e)) if c2.x() != c1.x() else 0.0
            cx = c1.x() + (c2.x() - c1.x()) * e
            cy = c1.y() + (c2.y() - c1.y()) * e + arc
            ghost = QRectF(c1.x() - CELL / 2, c1.y() - CELL / 2, CELL, CELL)
            self._draw_card(p, ghost, v, alpha=int(60 * (1.0 - e)))
            fly = QRectF(cx - CELL / 2, cy - CELL / 2, CELL, CELL)
            self._draw_card(p, fly, v, scale=1.0 + 0.06 * (1.0 - abs(0.5 - e) * 2.0))

        # pop-эффект слитых карт
        for at, v, u in self._popping:
            e = ease_out(u)
            rect = self.cell_rect(*at)
            sc = 0.35 + 0.65 * e
            if u < 0.55:
                sc *= 1.0 + 0.18 * (u / 0.55)
            self._draw_card(p, rect, v, scale=sc,
                            flash=int(350 * (1.0 - e)))

        # въезд новых карт/строк сверху
        for at, v, u in self._spawning:
            e = ease_out(u)
            x, y = at
            cy_target = PAD + y * (CELL + GAP) + CELL / 2
            start_y = -CELL / 2
            cy = start_y + (cy_target - start_y) * e
            rect = QRectF(PAD + x * (CELL + GAP), cy - CELL / 2, CELL, CELL)
            if y >= self.game.size[1]:   # умершая за краем карта — гаснет
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
        self.game = self._fresh_game()
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
                         "Drag — тащить срез мышью. Стрелки ←→ — перенести в соседнюю "
                         "колонку, ↑↓ — выбрать карту ниже/выше в стопке. "
                         "Space — новая строка, Z — отмена.")

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
        if self.board.anim_active:      # дождаться окончания анимации
            QTimer.singleShot(60, lambda: self.undo_last(reason))
            return
        st = self._undo_stack.pop()
        self.game = self._deserialize(st)
        self.board.game = self.game
        self.board.selected = None
        self.board.merge_flash.clear()
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
        # Исход доброса строки (движок внутри может добавить несколько строк
        # и объявить Game Over) предсказываем прогоном на независимой копии;
        # оригинал меняем только если копия вернула чистый успех.
        self.board.do_add_row()

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
        """Game Over, когда не осталось ни одного действия: ни принятого
        движком переноса (включая предсказанные «Game Over...»-ходы — они
        тоже действия, ими можно сознательно завершить партию), ни живого
        «+ Строка»."""
        if self.game_over:
            return
        if not self.board.has_any_action():
            self.on_game_over("не осталось ни ходов, ни места для новой строки")

    def new_game(self):
        if self.board.anim_active:
            QTimer.singleShot(60, self.new_game)
            return
        self.game = self._fresh_game()
        self.board.game = self.game
        self.board.selected = None
        self.board.merge_flash.clear()
        self.board.anim_active = False
        self.board.setMinimumSize(self.board.board_px_w(), self.board.board_px_h())
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
