# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) для игры "mergeCards" на PyQt5.

Игровая логика — mergeCards/API.py (класс Game). GUI ничего не «чинит»
в движке, но корректно его использует:

Управление мышью:
    - клик по ЛЮБОЙ карте       — выбрать её как источник; переносится
      выбранная карта и ВСЁ, что под ней (это ограничение движка);
    - drag (зажать и тащить)   — взять верхнюю карту стопки (или всю
      стопку целиком, если схватили за её тело) и бросить в колонку;
    - Ctrl+колесо / Up / Down   — выбрать карту НИЖЕ/ВЫШЕ в стопке,
      т.е. переносить не всю колонку, а только часть («определённые карты»);
    - Shift+клик                — сразу перенести ВЕСЬ столбец в цель;
    - стрелки Left / Right      — выбор цели для переноса с клавиатуры;
    - Пробел                    — добавить строку сверху (add_row);
    - Z                         — отменить последний ход;
    - R                         — новая игра;  Esc — выход.

Смерть (Game Over) наступает ТОЛЬКО когда не осталось ни одного хода
ни у переносов, ни у кнопки «+ Строка». Ходы, которые сами по себе
заполняют поле, разрешены — после них можно спастись новой строкой
(движок это позволяет), поэтому «застрять без возможности проиграть»
невозможно.
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

        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._on_anim_tick)

        self.setMinimumSize(self.board_px_w(), self.board_px_h())
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

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
        """Выживет ли игра после «+ Строка» (проверяется на копии)."""
        try:
            gc = self._clone()
            res = gc.add_row()
        except Exception:
            return False
        if isinstance(res, tuple):
            return False
        return not any(gc.field[x][self.game.size[1]].value
                       for x in range(self.game.size[0]))

    def has_any_action(self):
        if self.game_over_flag():
            return False
        # Ходы, которые сами заканчивают игру («Game Over...»), действиями
        # не считаются: иначе игра никогда не признает поражение.
        if any(m[4] == "Success" for m in self.legal_moves()):
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
        if src_x == dst_x:
            self.selected = None
            self.update()
            return False
        if count_rows is None:
            count_rows = self.selected_count() or 1
        r = self._try_move(src_x, src_y, dst_x, count_rows)
        if r is None:
            self.selected = None
            win.show_status("Такой перенос движок не принимает.", warn=True)
            self.update()
            return False
        res, gc, added = r
        before = {(x, y, v) for x, y, v in self._all_values()}
        win.push_undo()
        # применяем тот же ход к оригиналу (детерминированно, тем же путём)
        try:
            real_res, real_added = self.game.action_full(
                (src_x, src_y), dst_x, count=count_rows)
        except Exception as exc:
            win.pop_undo_discard()
            self.selected = None
            win.show_status(f"Ошибка хода: {exc}", warn=True)
            return False
        self.selected = None
        if isinstance(real_res, tuple):
            real_res = str(real_res[0])
        if real_res.startswith("Game Over"):
            # ход заполнил поле — но это ЧЕСТНЫЙ проигрыш: до него у игрока
            # всегда была альтернатива («+ Строка» или другой ход).
            win.on_game_over(real_res)
        elif real_res != "Success":
            win.undo_last(reason="Ход отвергнут движком.")
        else:
            self._detect_flashes(before)
            if added or real_added:
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
            self.update()
            return
        t = self.top_index(x)
        prev = self.selected          # выбор ДО этого нажатия
        if t is None:
            # клик по пустой колонке: если есть выбранный источник — переносим
            if prev is not None:
                self.try_move(prev[0], prev[1], x, self.selected_count())
            else:
                self.selected = None
                self.update()
            return
        if ev.modifiers() & Qt.ShiftModifier:
            # Shift+ЛКМ: выбрать ВЕСЬ столбец (перенести все его карты)
            self.selected = (x, t)
            self.drag_all_column = True
            self.drag_from = None     # для всей колонки работает только клик-пара
            self.update()
            return
        if prev is not None and prev[0] != x:
            # второй клик по ДРУГОЙ колонке = ход «источник -> эта колонка».
            # куда именно целимся (верх/низ стопки) не важно: движок всегда
            # кладёт пачку сверху на целевую колонку.
            self.try_move(prev[0], prev[1], x, self.selected_count())
            return
        # выбираем конкретную карту (если попали по занятой) — переносится
        # она и ВСЁ под ней; куда именно целимся (верх/низ стопки) не важно:
        # движок всегда кладёт пачку СВЕРХУ на целевую колонку.
        sy = y if self.value_at(x, y) else t
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
            self.update()
            return
        self.selected = (x, sy)
        self.drag_all_column = False
        self.drag_from = (x, sy)
        self.drag_pos = QPointF(ev.pos())
        self.update()

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
        """Ctrl+колесо — сдвиг выбранной карты вверх/вниз по стопке."""
        if self.selected is None:
            return
        x, y = self.selected
        t = self.top_index(x)
        b = self.bottom_index(x)
        if t is None:
            self.selected = None
            self.update()
            return
        dy = -1 if ev.angleDelta().y() > 0 else 1
        ny = max(t, min(b, y + dy))
        while ny != y and not self.value_at(x, ny):
            ny += 1 if ny < y else -1
        self.selected = (x, ny)
        self.update()

    def leaveEvent(self, ev):
        self.hover_col = None
        self.drag_from = None
        self.update()

    def keyPressEvent(self, ev):
        key = ev.key()
        if key in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            # аналог Ctrl+колеса с клавиатуры
            x, y = self.selected
            t, b = self.top_index(x), self.bottom_index(x)
            dy = -1 if key == Qt.Key_Up else 1
            ny = max(t, min(b, y + dy))
            self.selected = (x, ny)
            self.update()
            return
        if key in (Qt.Key_Left, Qt.Key_Right):
            dx = -1 if key == Qt.Key_Left else 1
            cols = self.game.size[0]
            if self.selected is None:
                cur = self.hover_col if self.hover_col is not None else 0
                for _ in range(cols):
                    cur = (cur + dx) % cols
                    if self.top_index(cur) is not None:
                        break
                if self.top_index(cur) is not None:
                    self.selected = (cur, self.top_index(cur))
            else:
                sx, sy = self.selected
                self.try_move(sx, sy, (sx + dx) % cols, self.selected_count())
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
        # Сначала проверим исход на КОПИИ — чтобы исключение движка
        # (KeyError при переполнении) не уронило приложение после получившегося
        # частичного изменения поля.
        try:
            probe = self.clone_game(self.game)
            res = probe.add_row()
        except Exception:
            self.on_game_over("поле переполнено — новую строку добавить некуда")
            self.refresh_stats()
            return
        if isinstance(res, tuple) and str(res[0]).startswith("Game Over"):
            self.on_game_over(str(res[0]))
            return
        self.push_undo()
        try:
            self.game.add_row()
        except Exception:
            self.undo_last(reason="Не удалось добавить строку.")
            self.on_game_over("поле переполнено")
            return
        self.board.row_slide = 1.0
        self.board._start_anim()
        self.show_status("Добавлена новая строка сверху.")
        self.refresh_stats()
        self.check_deadlock()

    def on_game_over(self, reason: str):
        self.game_over = True
        self.show_status(f"Игра окончена ({reason}). «Отменить» (Z) вернёт последний ход, "
                         "«Новая игра» или R — начнёт заново.", warn=True)

    def check_deadlock(self):
        """Game Over только когда НЕТ ни одного принятого движком хода
        И «+ Строка» тоже ведёт к заполнению поля."""
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
