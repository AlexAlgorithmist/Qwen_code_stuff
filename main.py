# -*- coding: utf-8 -*-
"""
Визуальный клиент (GUI) для игры "mergeCards" на PyQt5.

Игровая логика берётся из mergeCards/API.py (класс Game).
Управление:
    - Клик по колонке            — выбрать колонку-источник (берётся верхняя карта стопки);
      повторный клик по той же   — снять выбор;
    - Клик по другой колонке     — переместить всю стопку выбранной колонки в целевую;
    - Стрелки Left / Right       — то же самое с клавиатуры (выбор -> цель);
    - Пробел                     — добавить строку сверху (add_row);
    - R                          — новая игра;  Esc — выход.

Запуск:  python main.py
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
    from API import Game, pretty_number
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
    """Рисует поле Game и обрабатывает клики/перетаскивание колонок."""

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.selected_col = None          # выбранная колонка-источник
        self.hover_col = None             # колонка под курсором
        self.drag_from = None             # колонка, от которой начали перетаскивание
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

    def _col_at(self, pos: QPointF):
        for x in range(self.game.size[0]):
            r = self.col_rect(x)
            if r.contains(pos):
                return x
        return None

    # --- игровые действия --------------------------------------------
    def column_stack(self, x):
        """Уровни карт колонки x сверху вниз; индекс списка == координата y API.

        В API y = 0 — верхний видимый ряд: новые строки появляются именно
        сверху (_addrow сдвигает всё вниз), а «дно» колодца — это y = size[1]-1.
        Карты в колонке лежат подряд от какого-то y до дна.
        """
        vals = []
        for y in range(self.game.size_calc[1]):
            v = self.game.field[x][y].value
            if v:
                vals.append(v)
        return vals

    def top_index(self, x):
        """Координата y САМОЙ ВЕРХНЕЙ карты колонки (или None если пуста).

        action_full((x, y), dst) забирает из колонки x ВСЕ карты начиная с
        ряда y и ниже (y .. size[1]-1). Чтобы перенести всю стопку целиком,
        нужно передать y самой верхней занятой клетки.
        """
        for y in range(self.game.size_calc[1]):
            if self.game.field[x][y].value:
                return y
        return None

    def legal_moves(self):
        """Список ходов (src, dst), которые движок точно примет без Game Over.

        Проверка идёт на КОПИИ игры (action_full мутирует поле и может
        вернуть 'Game Over', не откатывая состояние), поэтому оригинал
        никогда не портится. Ход считается легальным, если копия отвечает
        'Success' и при этом поле после хода остаётся в «живом» состоянии
        (ни одна карта не вышла за нижний край поля).
        """
        moves = []
        if self.game_over_flag():
            return moves
        bottom = self.game.size[1] - 1
        for src in range(self.game.size[0]):
            top = self.top_index(src)
            if top is None:
                continue
            for dst in range(self.game.size[0]):
                if dst == src:
                    continue
                try:
                    gcopy = self.game.copy()
                    res, _ = gcopy.action_full((src, top), dst)
                except Exception:
                    continue
                if res != "Success":
                    continue
                if any(gcopy.field[x][bottom].value for x in range(self.game.size[0])):
                    continue  # ход привёл бы к переполнению — не предлагаем
                moves.append((src, dst))
        return moves

    def has_legal_move(self):
        return bool(self.legal_moves())

    def game_over_flag(self):
        win = self.window()
        return bool(win is not None and getattr(win, "game_over", False))

    def try_move(self, src: int, dst: int):
        """Выполняет ход «перенести всю стопку колонки src в колонку dst».

        Перед вызовом движка ход проверяется на копии (legal_moves), чтобы
        GUI никогда не уводил игру в нереверсивный Game Over из-за клика.
        """
        if src is None or dst is None or src == dst:
            self.selected_col = None
            self.update()
            return False
        win = self.window()
        if getattr(win, "game_over", False):
            return False
        top = self.top_index(src)
        if top is None:
            self.selected_col = None
            self.update()
            return False
        if (src, dst) not in self.legal_moves():
            self.selected_col = None
            win.show_status("Такой ход приведёт к заполнению поля — выберите другую цель.",
                            warn=True)
            self.update()
            return False
        before = {(x, y, v) for (x, y, v) in self._all_values()}
        try:
            res, added = self.game.action_full((src, top), dst)
        except Exception as exc:  # движок не должен падать, но подстрахуемся
            self.selected_col = None
            win.show_status(f"Ошибка хода: {exc}", warn=True)
            return False
        self.selected_col = None
        if isinstance(res, str) and res.startswith("Game Over"):
            win.on_game_over(res)
        elif res != "Success":
            win.show_status("Нельзя так ходить! (стопка не помещается)", warn=True)
        else:
            self._detect_flashes(before)
            if added:
                self.row_slide = 1.0
            combo = max(self.game.lastCombo) if self.game.lastCombo else 0
            msg = "Ход сделан"
            if combo > 1:
                msg = f"Комбо x{combo}!"
            win.show_status(msg)
        win.refresh_stats()
        win.check_deadlock()
        self._start_anim()
        self.update()
        return True

    def _all_values(self):
        out = []
        for x in range(self.game.size[0]):
            for y in range(self.game.size[1]):
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
    # Управление колодцей (стопкой) — кликом по её ВЕРХНЕЙ карте:
    #   * клик по верхней карте колонки A — выбор;
    #   * затем клик по верхней карте колонки B — перенос всей стопки A в B;
    #   * повторный клик по той же верхней карте — снять выбор.
    # Клик по «телу» стопки (не по верхней карте) не является ходом,
    # поэтому он только подсвечивает колонку, но не выбирает её как источник.
    def _cell_at(self, pos):
        """(col, api_y) клетки под курсором или (None, None).

        Экранная строка 0 (самая верхняя) == y_api 0 — инверсии нет.
        """
        for x in range(self.game.size[0]):
            if not (self.col_rect(x).contains(pos)):
                continue
            row = int((pos.y() - PAD) // (CELL + GAP))
            if 0 <= row <= self.game.size[1] - 1:
                return x, row
            return None, None
        return None, None

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        x, y = self._cell_at(ev.pos())
        if x is None:
            self.selected_col = None
            self.update()
            return
        top = self.top_index(x)
        if top is not None and abs(y - top) <= 1:
            # клик по верхней карте (или впритык под ней — зона «схвата» стопки)
            if self.selected_col is None:
                self.selected_col = x
            elif self.selected_col == x:
                self.selected_col = None          # отмена выбора
            else:
                self.try_move(self.selected_col, x)
        elif self.selected_col is not None and top is None:
            # цель пуста — переносим стопку в пустую колонку целиком
            self.try_move(self.selected_col, x)
        else:
            # клик мимо верхней карты — просто снимаем выбор/подсвечиваем
            self.selected_col = None
            self.hover_col = x
        self.update()

    def mouseMoveEvent(self, ev):
        self.hover_col = self._col_at(ev.pos())
        self.update()

    def leaveEvent(self, ev):
        self.hover_col = None
        self.update()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Left, Qt.Key_Right):
            dx = -1 if ev.key() == Qt.Key_Left else 1
            cols = self.game.size[0]
            if self.selected_col is None:
                cur = self.hover_col if self.hover_col is not None else 0
                for _ in range(cols):
                    cur = (cur + dx) % cols
                    if self.column_stack(cur):
                        break
                if self.column_stack(cur):
                    self.selected_col = cur
            else:
                self.try_move(self.selected_col, (self.selected_col + dx) % cols)
            self.update()
        elif ev.key() == Qt.Key_Escape:
            self.selected_col = None
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

        # подсветки/селекторы колонок
        for x in range(self.game.size[0]):
            r = self.col_rect(x).adjusted(2, 2, -2, -2)
            if x == self.selected_col:
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

        # danger: карты дошли до нижнего ряда (y = size[1]-1) — поле заполнено
        bottom_row = self.game.size[1] - 1
        danger = any(self.game.field[x][bottom_row].value
                     for x in range(self.game.size[0]))

        # карты
        for x in range(self.game.size[0]):
            stack = self.column_stack(x)
            n = len(stack)
            if not n:
                continue
            y_first = self.top_index(x)          # y самой верхней карты
            for i, v in enumerate(stack):
                y = y_first + i                  # координата API == экранная строка
                center = self.cell_center(x, y)
                slide_off = 0.0
                if self.row_slide > 0 and y == 0:
                    # новая строка «въезжает» сверху: рисуем её со смещением вверх
                    slide_off = -self.row_slide * (CELL + GAP)
                rect = QRectF(center.x() - CELL / 2,
                              center.y() - CELL / 2 + slide_off,
                              CELL, CELL)
                scale = 1.0
                if x == self.selected_col:
                    scale = 1.0 + 0.03 * (i + 1) / max(n, 1)
                self._draw_card(p, rect, v, scale=scale,
                                flash=self.merge_flash.get((x, y), 0))

        if danger:
            pen = QPen(QColor(239, 93, 93, 220))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            # линия ПОД нижним рядом — зона переполнения
            line_y = PAD + self.game.size[1] * (CELL + GAP) - GAP + 4
            p.drawLine(int(PAD), int(line_y),
                       int(self.board_px_w() - PAD), int(line_y))

        p.end()

    def _draw_card(self, p: QPainter, rect: QRectF, value: int,
                   scale: float = 1.0, flash: int = 0):
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
        c2 = c1.darker(135)
        lg = QLinearGradient(r.topLeft(), r.bottomRight())
        lg.setColorAt(0, c1.lighter(112))
        lg.setColorAt(1, c2)

        # тень
        p.setPen(Qt.NoPen)
        shadow = QRectF(r).translated(0, 3)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRoundedRect(shadow, 14, 14)

        p.setBrush(lg)
        p.drawRoundedRect(r, 14, 14)

        # блик
        path = QPainterPath()
        path.addRoundedRect(QRectF(r.x(), r.y(), r.width(), r.height() * 0.45), 14, 14)
        gloss = QLinearGradient(r.topLeft(), QPointF(r.left(), r.center().y()))
        gloss.setColorAt(0, QColor(255, 255, 255, 55))
        gloss.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, gloss)

        if flash:
            alpha = int(140 * (flash / 350.0))
            p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(r, 14, 14)

        label = card_label(value)
        f = QFont("Segoe UI", 16, QFont.Bold)
        if len(label) > 5:
            f.setPointSize(11)
        if len(label) > 8:
            f.setPointSize(9)
        p.setFont(f)
        p.setPen(QColor(text))
        p.drawText(r, Qt.AlignCenter, label)

        # метка уровня
        f2 = QFont("Consolas", 8)
        p.setFont(f2)
        p.setPen(QColor(text).lighter(140) if QColor(text).value() < 160
                 else QColor(text).darker(140))
        p.drawText(r.adjusted(6, 2, -6, -2), Qt.AlignTop | Qt.AlignLeft, f"L{value}")


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Merge Cards — PyQt5")
        self.setStyleSheet("QMainWindow { background: #0b0e18; }")
        self.game_over = False

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
        self.game = Game((BOARD_COLS, BOARD_ROWS), {})
        self.game.add_row()
        self.board = BoardWidget(self.game)
        root.addWidget(self.board, 0, Qt.AlignHCenter)

        # ------- статус -------
        self.lbl_status = QLabel("Выберите колонку, затем цель — карты сливаются пополам.")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color:#9aa7c7; font: 13px 'Segoe UI'; padding:4px;")
        root.addWidget(self.lbl_status)

        # ------- кнопки -------
        btns = QHBoxLayout()
        btns.addStretch(1)
        b_add = QPushButton("+ Строка")
        b_new = QPushButton("Новая игра")
        for b in (b_add, b_new):
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
        b_new.clicked.connect(self.new_game)

        self.refresh_stats()
        self.show_status("Удачи! Клик по колонке — выбор, клик по другой — перенос всей стопки.")

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
        # Game.add_row() возвращает None при успехе и ("Game Over...", True) при провале.
        # При нехватке места движок может бросить исключение — ловим его и
        # корректно завершаем игру вместо падения приложения.
        try:
            res = self.game.add_row()
        except Exception:
            self.on_game_over("поле переполнено")
            self.refresh_stats()
            return
        if isinstance(res, tuple) and str(res[0]).startswith("Game Over"):
            self.on_game_over(res[0])
        else:
            self.board.row_slide = 1.0
            self.board._start_anim()
            self.show_status("Добавлена новая строка сверху.")
        self.refresh_stats()
        self.check_deadlock()

    def on_game_over(self, reason: str):
        self.game_over = True
        self.show_status(f"Игра окончена ({reason}). Нажмите «Новая игра» или R.", warn=True)

    def check_deadlock(self):
        """Если ходов больше нет — корректно завершаем игру (без падения)."""
        if self.game_over:
            return
        if not self.board.has_legal_move():
            self.on_game_over("нет доступных ходов")

    def new_game(self):
        self.game = Game((BOARD_COLS, BOARD_ROWS), {})
        self.game.add_row()
        self.board.game = self.game
        self.board.selected_col = None
        self.board.merge_flash.clear()
        self.board.setMinimumSize(self.board.board_px_w(), self.board.board_px_h())
        self.game_over = False
        self.refresh_stats()
        self.show_status("Новая игра началась!")
        self.board.update()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self.add_row_clicked()
        elif ev.key() in (Qt.Key_R,):
            self.new_game()
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
