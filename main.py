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

import os
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
# Виджет игрового поля
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    """Рисует поле Game и обрабатывает клики/drag-and-drop колонок."""

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected = None              # (x, y) — выбранная карта-источник
        self.hover_col = None             # колонка под курсором
        self.drag_from = None             # (x, y) — откуда начали перетаскивание
        self.drag_all_column = False      # тянем весь столбец?
        self.drag_pos = None              # позиция курсора во время перетаскивания
        self.merge_flash = {}             # (x, y) -> оставшееся время вспышки, мс
        self.row_slide = 0.0              # прогресс анимации сдвига строки (0..1)
        self.pending_confirm = None       # (sx, sy, dx, count) — ход ждёт подтверждения

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
        """Совершает перенос; возвращает True, если ход был принят."""
        win = self.window()
        if win is None:
            return False
        if getattr(win, "game_over", False):
            return False
        if count_rows is None:
            count_rows = self.slice_count(src_x, src_y) or 1
        if src_x == dst_x or count_rows <= 0:
            self.selected = None
            self.pending_confirm = None
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
            self.pending_confirm = None
            win.show_status("Движок отвергает такой перенос "
                            "(источник пуст или позиция недопустима).",
                            warn=True)
            self.update()
            return False
        if res_probe.startswith("Game Over"):
            # Голый движок на таком ходе просто возвращает "Game Over..." и
            # оставляет поле в финальном состоянии. Делаем ровно то же:
            # применяем результат прогона на копии и объявляем поражение —
            # без «подтверждений», которые раньше делали игру непроигрываемой.
            self.pending_confirm = None
            win.push_undo()
            self.game = gc_probe
            win.board_game_changed(self.game)
            win.on_game_over(res_probe)
            win.refresh_stats()
            self._start_anim()
            self.update()
            return True
        # Ход «Success» на копии — применяем его к оригиналу. Если движок
        # при ходе НЕ добавлял новую строку, он детерминирован и результат
        # в точности совпадает с движком без оболочки; если строку добавил,
        # там есть randint — финальное состояние берём с проверенной копии,
        # чтобы экран показывал ровно тот исход, который был предсказан.
        before = {(x, y, v) for x, y, v in self._all_values()}
        win.push_undo()
        # Применяем тот же ход к ЧИСТОМУ оригиналу. Если движок при этом
        # сам объявил Game Over или упал (ранее мы это предсказали по
        # детерминированной части) — просто берём финальное состояние
        # проверенной копии: игрок видит ровно то, что ему обещали.
        try:
            real_res, real_added = self.game.action_full(
                (src_x, src_y), dst_x, count=count_rows)
        except Exception:
            real_res, real_added = "Game Over: engine exception", False
        if isinstance(real_res, tuple):
            real_res = str(real_res[0])
        if real_res != "Success":
            self.game = gc_probe
            win.board_game_changed(gc_probe)
            added = added_probe
        else:
            added = real_added or added_probe
            if added and added_probe:
                # ход вызвал доброс строки (внутри randint) — сверяемся с
                # копией только если оригинал и копия разошлись
                same = (all(self.game.field[x][y].value == gc_probe.field[x][y].value
                            for x in range(self.game.size[0])
                            for y in range(self.game.size_calc[1]))
                        and self.game.points == gc_probe.points)
                if not same:
                    self.game = gc_probe
                    win.board_game_changed(gc_probe)
        self.selected = None
        if str(real_res).startswith("Game Over"):
            # честный проигрыш: до него у игрока всегда была альтернатива
            # («+ Строка» или другой ход).
            win.on_game_over(str(real_res))
        else:
            self._detect_flashes(before)
            if added:
                self.row_slide = 1.0
            combo = max(self.game.lastCombo) if self.game.lastCombo else 0
            win.show_status(f"Комбо x{combo}!" if combo > 1 else
                            f"Перенесено карт: {count_rows}.")
        win.refresh_stats()
        win.check_deadlock()
        self._start_anim()
        self.update()
        return True

    def _all_values(self):
        out = []
        for x in range(self.game.size[0]):
            for y in range(self.game.size_calc[1]):
                v = self.game.field[x][y].value
                if v:
                    out.append((x, y, v))
        return out

    def _detect_flashes(self, before_set):
        """После хода помечаем клетки, где появились новые (слитые) уровни."""
        for (x, y, v) in self._all_values():
            if (x, y, v) not in before_set:
                self.merge_flash[(x, y)] = 350

    # --- анимация ------------------------------------------------------
    def _start_anim(self):
        if not self._anim.isActive():
            self._anim.start()

    def _on_anim_tick(self):
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
            self.pending_confirm = None
            self.update()
            return
        vis = self.visible_cards(x)
        prev = self.selected          # выбор ДО этого нажатия
        self.pending_confirm = None
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
            self.drag_from = None     # для всей колонки работает только клик-пара
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
            self.pending_confirm = None
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
                self.pending_confirm = None
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
            self.pending_confirm = None
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

        # карты
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

        # призрак перетаскиваемой пачки
        if self.drag_from is not None and self.drag_pos is not None:
            gx, gy = self.drag_pos.x(), self.drag_pos.y()
            moved = False
            g0 = self.cell_center(*self.drag_from)
            if abs(gx - g0.x()) > 12 or abs(gy - g0.y()) > 12:
                moved = True
            if moved:
                cnt = self.selected_count()
                x, y = self.drag_from
                vals = []
                yy = y
                while self.value_at(x, yy) and len(vals) < cnt:
                    vals.append(self.value_at(x, yy))
                    yy += 1
                for i, v in enumerate(vals):
                    rect = QRectF(gx - CELL / 2, gy - CELL / 2 + i * (CELL + GAP) * 0.35,
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
        if bad:
            # Как в голом движке: показываем финальное состояние и
            # объявляем поражение сразу, без «подтверждений».
            self.push_undo()
            self.game = probe
            self.board.game = self.game
            self.board.selected = None
            self.board.row_slide = 1.0
            self.board._start_anim()
            self.on_game_over(str(res) if res is not None
                              else "строка заполнила поле")
            self.refresh_stats()
            self.board.update()
            return
        self.push_undo()
        self.game = probe
        self.board.game = self.game
        self.board.row_slide = 1.0
        self.board._start_anim()
        self.show_status("Добавлена новая строка сверху.")
        self.refresh_stats()
        self.check_deadlock()
        self.board.update()

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
        self.game = self._fresh_game()
        self.board.game = self.game
        self.board.selected = None
        self.board.merge_flash.clear()
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
