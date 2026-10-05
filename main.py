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
# Планировщик анимаций: превращает пару снимков «до/после» в набор фаз
# ---------------------------------------------------------------------------

class MovePlanner:
    """Сравнивает снимки поля (dict {(x, y): value}) до и после хода движка
    и строит план анимации.

    Возвращаемый формат: список фаз [(ms, events), ...], где event — dict:
      fly:    {'kind':'fly','frm':(x,y),'to':(x,y),'v':val,'delay':ms}
      vanish: {'kind':'vanish','at':(x,y),'v':val,'delay':ms}
      pop:    {'kind':'pop','at':(x,y),'v':val,'delay':ms}
      spawn:  {'kind':'spawn','at':(x,y),'v':val,'delay':ms}   (новая карта)
      slide:  {'kind':'slide','frm':(x,y),'to':(x,y),'v':val,'delay':ms}
              (старая карта осела вниз под новые строки)
    """

    @staticmethod
    def _landing_probe(before: dict, slice_cells, dst_x: int):
        """Клетки, куда лёг бы переносимый срез БЕЗ слияний и доброса строк
        (чистая механика action_full: пачка кладётся подряд под нижнюю
        занятую клетку колонки-цели). Копия движка полностью независима:
        _merge/_compress/_can_merge заглушены."""
        try:
            g = Game((BOARD_COLS, BOARD_ROWS), {}, _bag=[1])
            g._merge = lambda *a, **kw: False
            g._compress = lambda *a, **kw: False
            g._can_merge = lambda *a, **kw: True
            for (x, y), v in before.items():
                if v and 0 <= x < BOARD_COLS and 0 <= y < BOARD_ROWS:
                    g.field[x][y].value = v
            sy = min(c[1] for c in slice_cells)
            res, _added = g.action_full((slice_cells[0][0], sy), dst_x,
                                        count=len(slice_cells))
            if isinstance(res, tuple) or str(res) != "Success":
                return []
            taken = {(dst_x, y) for y in range(BOARD_ROWS)
                     if g.field[dst_x][y].value}
            return sorted(taken - set(slice_cells), key=lambda c: c[1])
        except Exception:
            return []

    @staticmethod
    def plan(before: dict, after: dict, src_x=None, dst_x=None):
        # --- 1. сопоставление карт «до» -> клетки «после» (жадно, по приоритету) ---
        flights = []          # (frm, to, v) — перенесённый срез летит в dst
        slides = []           # (frm, to, v) — старая карта осела вниз/уехала
        stayers = set()       # (x, y) — карта осталась на месте
        matched_after = set()
        moved_src = set()

        def take_value(col, val, used_rows):
            """Первая незанятая клетка колонки col со значением val из after."""
            for y in sorted(used_rows[val]):
                if (col, y) in after and after[(col, y)] == val \
                        and (col, y) not in matched_after:
                    return (col, y)
            return None

        by_val = {}
        for p, v in after.items():
            if v:
                by_val.setdefault(v, set()).add(p[1])

        if src_x is not None and dst_x is not None:
            src_cells = sorted([p for p, v in before.items()
                                if v and p[0] == src_x], key=lambda p: p[1])
            # пачка ложится под нижнюю карту цели подряд; перебираем все
            # варианты «сколько верхних карт среза выжило» и берём тот, где
            # честные перелёты совпадают со значениями after (съеденные
            # слиянием карты просто не имеют пары — они улетают «в схлопывание»)
            best_cfg = None
            for skip in range(len(src_cells), -1, -1):
                survivors = src_cells[skip:]
                if not survivors:
                    cfg = []
                    ok = True
                    # цель пустая? тогда skip==len допустим только если в
                    # after нет клеток dst, которые могли бы быть нашими
                    cfg = []
                    best_cfg = cfg
                    continue
                land_y = max([p[1] for p, v in after.items()
                              if v and p[0] == dst_x] + [-1]) + 1 - len(survivors)
                cfg = []
                ok = True
                for i, sp in enumerate(survivors):
                    tp = (dst_x, land_y + i)
                    if after.get(tp) != before[sp]:
                        ok = False
                        break
                    cfg.append((sp, tp))
                if ok and (best_cfg is None or len(cfg) > len(best_cfg)):
                    best_cfg = cfg
            # добавляем также вариант без «выживших», но с частичным match
            if best_cfg is None:
                best_cfg = []
            # поверх best_cfg попробуем удлинить за счёт любых честных
            # соответствий src->dst (на случай нестандартных раскладок)
            claimed = {c[0] for c in best_cfg}
            for sp in src_cells:
                if sp in claimed:
                    continue
                tv = before[sp]
                tp = take_value(dst_x, tv, by_val)
                if tp:
                    best_cfg.append((sp, tp))
                    claimed.add(sp)
            for sp, tp in best_cfg:
                matched_after.add(tp)
                moved_src.add(sp)
                flights.append((sp, tp, before[sp]))

        # остальные карты: остаться / съехать (только вниз или горизонтально
        # к своей новой позиции после компрессии)
        rest = sorted([p for p, v in before.items()
                       if v and p not in moved_src], key=lambda p: (-p[1], p[0]))
        for fp in rest:
            fv = before[fp]
            if after.get(fp) == fv and fp not in matched_after:
                matched_after.add(fp)
                stayers.add(fp)
                continue
            cand = None
            for tp, tv in after.items():
                if tv != fv or tp in matched_after:
                    continue
                d = abs(tp[1] - fp[1]) + (abs(tp[0] - fp[0]) * 4 if tp[0] != fp[0] else 0)
                if cand is None or d < cand[0]:
                    cand = (d, tp)
            if cand:
                matched_after.add(cand[1])
                slides.append((fp, cand[1], fv))

        vanished = [p for p, v in before.items()
                    if v and p not in stayers and p not in moved_src
                    and p not in {s[0] for s in slides}]
        appeared = {p: v for p, v in after.items() if v and p not in matched_after}

        # --- 2. слияния: новая клетка + рядом исчезнувшая карта уровнем ниже ---
        merges = []           # ([parts], result_cell, value)
        used_parts = set()
        for ap in sorted(appeared, key=lambda p: (p[1], p[0])):
            av = appeared[ap]
            if av <= 1:
                continue
            parts = []
            for nb in ((ap[0], ap[1] - 1), (ap[0], ap[1] + 1)):
                if nb in vanished and nb not in used_parts and before[nb] == av - 1:
                    parts.append(nb)
            if parts:
                for q in parts:
                    used_parts.add(q)
                merges.append((parts, ap, av))
        merged_results = {r for _p, r, _v in merges}
        merge_parts = used_parts
        vanish_rest = [p for p in vanished if p not in merge_parts]
        new_cells = {p: v for p, v in appeared.items() if p not in merged_results}

        # --- 3. тайминги: фазы идут последовательно, внутри фазы стаг ---
        phases = []
        if flights:
            flights.sort(key=lambda it: it[1][1])     # верх летит первым
            ev = []
            t = 0.0
            for i, (frm, to, v) in enumerate(flights):
                ev.append({'kind': 'fly', 'frm': frm, 'to': to, 'v': v,
                           'delay': round(t)})
                t += 70.0
            phases.append((BoardWidget.FLY_MS, ev))

        if vanish_rest or merge_parts:
            ev = [{'kind': 'vanish', 'at': p, 'v': before[p],
                   'delay': round(i * 25.0)}
                  for i, p in enumerate(sorted(set(vanish_rest) | merge_parts,
                                               key=lambda c: c[1]))]
            phases.append((BoardWidget.VANISH_MS, ev))

        if merges:
            ev = [{'kind': 'pop', 'at': r, 'v': v, 'delay': round(i * 90.0)}
                  for i, (_p, r, v) in enumerate(merges)]
            phases.append((BoardWidget.POP_MS, ev))

        if slides:
            slides.sort(key=lambda it: -it[0][1])     # снизу вверх
            ev = [{'kind': 'slide', 'frm': f, 'to': t_, 'v': v,
                   'delay': round(i * 20.0)}
                  for i, (f, t_, v) in enumerate(slides)]
            phases.append((BoardWidget.SLIDE_MS, ev))

        if new_cells:
            # каскад: чем выше строка въезжает, тем раньше стартует;
            # ВСЕ добавленные движком строки анимируются, а не одна
            rows = sorted({p[1] for p in new_cells})
            rank = {r: i for i, r in enumerate(rows)}
            ev = [{'kind': 'spawn', 'at': p, 'v': v,
                   'delay': round(rank[p[1]] * 110.0 + p[0] * 12.0)}
                  for p, v in sorted(new_cells.items(), key=lambda it: (it[0][1], it[0][0]))]
            phases.append((BoardWidget.SPAWN_MS + rank[rows[-1]] * 110.0, ev))

        # гарантируем непустой план (ход без видимых изменений)
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
        phases = MovePlanner.plan(before, after, src_x, dst_x)
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

        p.end()

    def _paint_animated(self, p: QPainter, sel_x, sel_y):
        """Кадр анимированного перехода между снимками _base и _after."""
        moving_src = {f for f, _t, _v in self._flights}
        sliding_frm = {f for f, _t, _v in self._sliding}
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
