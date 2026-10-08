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
# Планировщик: журнал событий движка -> фазы анимации
# ---------------------------------------------------------------------------

class AnimScheduler:
    """Превращает журнал событий движка в последовательность фаз анимации.

    События идут в порядке исполнения движком; для наглядности они
    группируются в фазы:
        fly    — полёт перенесённой серии (синтезируется из хода, т.к.
                 движок перемещает карты прямым присваиванием без вызовов);
        vanish — схлопывание пар; pop — рождение результата слияния;
        slide  — перемещение существующей карты (каскад при добросе,
                 compress после слияний);
        spawn  — появление карт новой строки сверху.

    В конце фазы приводятся в согласованный вид со снимками before/after:
    лишние события отбрасываются, недостающие (карты, которые движок
    переставил «молча») добавляются как slide/spawn. Поэтому проигрывание
    фаз клетка-в-клетку приводит состояние «до» к состоянию «после» —
    анимация не может разъехаться с реальным полем.
    """

    FLY_MS = 430.0
    VANISH_MS = 260.0
    POP_MS = 260.0
    SLIDE_MS = 320.0
    SPAWN_MS = 400.0
    STAGGER = 55.0          # задержка между картами одной фазы, мс

    @staticmethod
    def build(events, before, after, src_x=None, dst_x=None, series=None):
        S1 = BOARD_ROWS
        phases = []

        # 1) полёт серии — перед всеми событиями журнала
        if src_x is not None and dst_x is not None and series:
            lo, hi = series
            dest_h = sum(1 for (x, y), v in before.items()
                         if x == dst_x and v and y < S1)
            flights = [{'kind': 'fly', 'frm': (src_x, y),
                        'to': (dst_x, dest_h + i),
                        'v': before.get((src_x, y), 0)}
                       for i, y in enumerate(range(lo, hi + 1))
                       if before.get((src_x, y), 0)]
            if flights:
                phases.append(['fly',
                               AnimScheduler.FLY_MS
                               + min(len(flights) * 70.0, 350.0),
                               flights])

        # 2) события журнала, группируя одноимённые подряд идущие
        for ev in events:
            op = ev['op']
            if op == 'slide':
                kind, data = 'slide', {'kind': 'slide',
                                       'frm': tuple(ev['frm']),
                                       'to': tuple(ev['to']), 'v': ev['v']}
            elif op == 'vanish':
                kind, data = 'vanish', {'kind': 'vanish',
                                        'at': tuple(ev['at']), 'v': ev['v']}
            elif op == 'pop':
                kind, data = 'pop', {'kind': 'pop',
                                     'at': tuple(ev['at']), 'v': ev['v']}
            elif op == 'spawn':
                kind, data = 'spawn', {'kind': 'spawn',
                                       'at': tuple(ev['at']), 'v': ev['v'],
                                       'row': ev.get('row', 0)}
            else:
                continue
            if phases and phases[-1][0] == kind:
                phases[-1][2].append(data)
            else:
                phases.append([kind, 0.0, [data]])

        AnimScheduler.reconcile(phases, before, after, S1)

        # 3) длительности и стаггеры внутри фаз
        res = []
        for kind, dur, evs in phases:
            if kind == 'mixed':
                # согласующая фаза: slide-части едут, spawn-части въезжают
                slides = [e for e in evs if e['kind'] == 'slide']
                spawns = [e for e in evs if e['kind'] == 'spawn']
                for i, e in enumerate(slides):
                    e['delay'] = round(i * 25.0)
                for i, e in enumerate(spawns):
                    e['delay'] = round(80 + i * 40.0)
                dur = max(AnimScheduler.SLIDE_MS + len(slides) * 25.0,
                          AnimScheduler.SPAWN_MS + len(spawns) * 40.0)
                res.append(('mixed', dur, evs))
                continue
            if kind == 'fly':
                evs.sort(key=lambda e: e['to'][1])
                for i, e in enumerate(evs):
                    e['delay'] = round(i * 60.0)
            elif kind == 'slide':
                evs.sort(key=lambda e: (-e['frm'][1], e['frm'][0]))
                dur = AnimScheduler.SLIDE_MS + min(len(evs) * 18.0, 220.0)
                rows = {}
                for e in evs:
                    e['delay'] = round(rows.get(e['frm'][1], 0) * 20.0)
                    rows[e['frm'][1]] = rows.get(e['frm'][1], 0) + 1
            elif kind == 'vanish':
                evs.sort(key=lambda e: (e['at'][1], e['at'][0]))
                dur = AnimScheduler.VANISH_MS + min(len(evs) * 50.0, 200.0)
                for i, e in enumerate(evs):
                    e['delay'] = round(i * AnimScheduler.STAGGER)
            elif kind == 'pop':
                evs.sort(key=lambda e: (e['at'][1], e['at'][0]))
                dur = AnimScheduler.POP_MS + min(len(evs) * 70.0, 280.0)
                for i, e in enumerate(evs):
                    e['delay'] = round(i * AnimScheduler.STAGGER)
            elif kind == 'spawn':
                rowrank = {r: i for i, r in
                           enumerate(sorted({e['row'] for e in evs}))}
                cols = {}
                for e in evs:
                    e['delay'] = round(rowrank[e['row']] * 120.0
                                       + cols.get(e['row'], 0) * 40.0)
                    cols[e['row']] = cols.get(e['row'], 0) + 1
                dur = (AnimScheduler.SPAWN_MS + max(rowrank.values()) * 120.0
                       if rowrank else AnimScheduler.SPAWN_MS)
            res.append((kind, dur, evs))
        return res

    # --- согласование с before/after --------------------------------------
    @staticmethod
    def _apply(field, kind, e, strict=False):
        """Одно событие на словаре {(x,y): v}. Возвращает True если применимо."""
        if kind == 'fly' or kind == 'slide':
            frm, to, v = e['frm'], e['to'], e['v']
            if field.get(frm) != v:
                return not strict          # молча пропускаем чужое событие
            del field[frm]
            field[to] = v
            return True
        if kind == 'vanish':
            at, v = e['at'], e['v']
            if field.get(at) != v:
                return not strict
            del field[at]
            return True
        if kind == 'pop':
            field[e['at']] = e['v']
            return True
        if kind == 'spawn':
            field[e['at']] = e['v']
            return True
        return False

    @staticmethod
    def reconcile(phases, before, after, S1):
        vis_after = {k: v for k, v in after.items() if v and k[1] < S1}
        # a) выбрасываем события, которые уже не сходятся с ходом истории.
        #    Особый случай: если перенесённая карта приземлилась точно на
        #    клетку vanish — схлопывается именно она (её «призрак» летит и
        #    гаснет в полёте), поэтому событие vanish для этой клетки убираем,
        #        а pop показываем как рождение результата на месте посадки.
        land = {}
        for ph in phases:
            if ph[0] == 'fly':
                for e in ph[2]:
                    land[e['to']] = e['v']
        field = {k: v for k, v in before.items() if v}
        for ph in phases:
            kept = []
            for e in ph[2]:
                f2 = dict(field)
                ok = True
                if ph[0] == 'vanish' and e['at'] in land \
                        and land.get(e['at']) == e['v']:
                    ok = False               # летящая карта сама слилась
                elif not AnimScheduler._apply(f2, ph[0], e, strict=True):
                    ok = False
                if ok:
                    field = f2
                    kept.append(e)
            ph[2] = kept
        # b) недостающие перемещения/рождения: то, что есть в after, но
        #    отсутствует в проигранной истории — добавляем отдельной фазой
        missing = {k: v for k, v in vis_after.items() if field.get(k) != v}
        extra = {k: v for k, v in field.items()
                 if v and k[1] < S1 and vis_after.get(k) != v}
        add_evs = []
        used = set()
        # сначала пытаемся объяснить недостающее сдвигом «лишней» карты
        for (ax, ay), av in sorted(extra.items()):
            cand = [(y, k) for k, y in
                    [((tx, ty), ty) for (tx, ty) in missing
                     if tx == ax and ty > ay and missing[(tx, ty)] == av]
                    if k not in used]
            if cand:
                ty = min(c[0] for c in cand)
                mk = (ax, ty)
                if mk in missing:
                    add_evs.append({'kind': 'slide', 'frm': (ax, ay),
                                    'to': mk, 'v': av})
                    used.add(mk)
                    del missing[mk]
                    extra.pop((ax, ay))
        for (mx, my), mv in sorted(missing.items()):
            add_evs.append({'kind': 'spawn', 'at': (mx, my), 'v': mv,
                            'row': my})
        if add_evs:
            phases.append(['mixed', 0.0, add_evs])
        # финальная проверка согласованности
        field = {k: v for k, v in before.items() if v}
        for ph in phases:
            for e in ph[2]:
                AnimScheduler._apply(field, ph[0], e)
        ok = all(field.get(k) == v for k, v in vis_after.items()) \
            and all(not field.get(k) for k in extra)
        return ok


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

        # плеер анимаций
        self.anim_active = False
        self._phases = []
        self._pi = 0
        self._pt = 0.0
        self._base = {}
        self._after = {}
        self._carry = set()
        self._flights = []
        self._vanishing = []
        self._popping = []
        self._sliding = []
        self._spawning = []
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
        res_probe, gc_probe, added_probe, evs_probe = probe
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
            self._play(before, after, evs_probe, src_x, dst_x, series,
                       gameover_msg=res_probe)
            win.refresh_stats()
            return True

        # Success на копии — применяем к оригиналу. Если движок НЕ добавлял
        # строку, ход детерминирован и результат совпадает с голым движком;
        # если добавил (там randint) — финалом берём проверенную копию,
        # чтобы экран показывал ровно предсказанный исход (и журнал событий
        # копии — тоже единственный честный).
        win.push_undo()
        real_res, real_added, real_evs = self._engine_move(
            self.game, src_x, src_y, dst_x, count_rows)
        msg = None
        if real_res != "Success":
            after = self.snapshot(gc_probe)
            self.game = gc_probe
            win.board_game_changed(gc_probe)
            msg = str(real_res)
            evs = evs_probe
        else:
            added = bool(real_added) or bool(added_probe)
            if added:
                same = (self.snapshot(self.game) == self.snapshot(gc_probe)
                        and self.game.points == gc_probe.points)
                if not same:
                    self.game = gc_probe
                    win.board_game_changed(gc_probe)
                    evs = evs_probe
                else:
                    evs = real_evs
            else:
                evs = real_evs
            after = self.snapshot(self.game)

        self.selected = None
        combo = max(self.game.lastCombo) if self.game.lastCombo else 0
        if msg is None:
            win.show_status(f"Комбо x{combo}!" if combo > 1 else
                            f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        self._play(before, after, evs, src_x, dst_x, series, gameover_msg=msg)
        if msg is None:
            win.check_deadlock()
        return True

    def do_add_row(self):
        """«+ Строка»: прогон на независимой копии с захватом журнала,
        затем анимация всего произошедшего (движок может добавить несколько
        строк сразу — анимируются ВСЕ)."""
        win = self.window()
        if win is None or getattr(win, "game_over", False) or self.anim_active:
            return
        probe = self._clone()
        try:
            with EventRecorder(probe) as rec:
                res = probe.add_row()
            evs = list(rec.events)
        except Exception:
            res = "Game Over: engine exception"
            evs = []
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
        self._play(before, after, evs, None, None, None, gameover_msg=msg)
        if not bad:
            win.check_deadlock()

    # --- плеер анимаций -----------------------------------------------------
    def _play(self, before, after, events, src_x, dst_x, series,
              gameover_msg=None):
        """Собирает фазы из ЖУРНАЛА движка (согласованные с before/after)
        и запускает плеер."""
        phases = AnimScheduler.build(events, before, after,
                                     src_x, dst_x, series)
        self._phases = phases
        self._pi = 0
        self._pt = 0.0
        self._base = {k: v for k, v in before.items() if v}
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

    def _phase_carry(self, pi):
        """Клетки, закрытые завершённой фазой (не рисуем базовым слоем)."""
        kind, _dur, evs = self._phases[pi]
        out = set()
        for ev in evs:
            k = ev['kind'] if kind == 'mixed' else kind
            if k in ('fly', 'slide'):
                out.add(ev['frm'])
                out.add(ev['to'])
            else:
                out.add(ev['at'])
        return out

    def _build_frame(self):
        """Собирает события текущего кадра из активной фазы."""
        self._reset_phase_state()
        if not (0 <= self._pi < len(self._phases)):
            return
        for pi in range(self._pi):
            self._carry |= self._phase_carry(pi)
        kind, dur, evs = self._phases[self._pi]
        for ev in evs:
            ekind = ev['kind'] if kind == 'mixed' else kind
            delay = ev.get('delay', 0.0)
            uu = (self._pt - delay) / max(dur - delay, 1.0)
            if uu < 0.0:
                if kind == 'fly':
                    # ещё не стартовала: остаётся на СТАРОМ месте
                    self._flights.append((ev['frm'], ev['frm'],
                                          ev['v'] or self._base.get(ev['frm'], 0),
                                          0.0))
                    self._carry.add(ev['frm'])
                continue
            uu = min(1.0, uu)
            if ekind == 'fly':
                v = ev['v'] or self._base.get(ev['frm'], 0)
                self._flights.append((ev['frm'], ev['to'], v, uu))
                self._carry.add(ev['frm'])
                if uu >= 1.0:
                    self._carry.add(ev['to'])
            elif ekind == 'vanish':
                self._vanishing.append((ev['at'], ev['v'], uu))
                if uu >= 1.0:
                    self._carry.add(ev['at'])
            elif ekind == 'pop':
                self._popping.append((ev['at'], ev['v'], uu))
                self._carry.add(ev['at'])
            elif ekind == 'slide':
                self._sliding.append((ev['frm'], ev['to'], ev['v'], uu))
                self._carry.add(ev['frm'])
                if uu >= 1.0:
                    self._carry.add(ev['to'])
            elif ekind == 'spawn':
                self._spawning.append((ev['at'], ev['v'], uu))
                self._carry.add(ev['at'])

    def _on_anim_tick(self):
        if not self.anim_active:
            return
        self._pt += self.TICK_MS
        while self._pi < len(self._phases) and \
                self._pt >= self._phases[self._pi][1]:
            self._carry |= self._phase_carry(self._pi)
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
        """Кадр анимированного перехода между _base и _after.

        Базовым слоем рисуется состояние «до» за вычетом клеток, затронутых
        фазами; поверх — активные события. Так кадры никогда не показывают
        «гибрид» до/после в одном месте доски.
        """
        moving_frm = {f for f, _t, _v, _u in self._flights}
        sliding_frm = {f for f, _t, _v, _u in self._sliding}
        active_to = ({t for _f, t, _v, _u in self._flights if _u >= 1.0}
                     | {t for _f, t, _v, _u in self._sliding if _u >= 1.0})
        vanish_at = {a for a, _v, _u in self._vanishing}
        pop_at = {a for a, _v, _u in self._popping}
        spawn_at = {a for a, _v, _u in self._spawning}

        # статичные карты состояния «до» (не затронутые анимацией)
        for (x, y), v in self._base.items():
            if not v or y < 0 or y >= self.game.size[1]:
                continue
            if (x, y) in (moving_frm | sliding_frm | self._carry):
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

        # въезд новых карт/строк сверху (все добавленные движком строки)
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
