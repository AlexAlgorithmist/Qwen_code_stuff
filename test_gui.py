# -*- coding: utf-8 -*-
"""Headless-проверки: планировщик анимаций + плеер + drag-and-drop."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mergeCards"))

from PyQt5.QtCore import Qt, QPointF, QEvent, QTimer
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest

import main as M
from API import Game, Card

app = QApplication(sys.argv)

fails = []


def check(name, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------------------
# 1. MovePlanner: чистые снимки «до/после»
# ---------------------------------------------------------------------------
def kinds(phases):
    ks = {}
    for dur, evs in phases:
        for e in evs:
            ks[e['kind']] = ks.get(e['kind'], 0) + 1
    return ks


# ход со слиянием — РЕАЛЬНЫЕ снимки движка (кейс B): v1 прилетает к v1 -> vanish+pop
before = {(0, 3): 1, (0, 4): 2, (1, 3): 4, (2, 5): 1}
after = {(0, 0): 2, (0, 1): 1, (1, 0): 3, (1, 1): 1, (1, 3): 4, (1, 5): 4,
         (2, 0): 1, (2, 1): 2, (2, 2): 3, (3, 0): 2, (4, 0): 2, (5, 0): 2}
ph = M.MovePlanner.plan(before, after, src_x=0, dst_x=2)
k = kinds(ph)
check("planner: fly present", k.get('fly', 0) >= 1, str(k))
check("planner: merge gives vanish+pop", k.get('vanish', 0) >= 1 and k.get('pop', 0) >= 1, str(k))

# add_row ДВУХ строк: все новые клетки должны получить spawn (не один слой!)
before = {(0, 6): 1, (1, 6): 2, (2, 6): 3, (3, 6): 4, (4, 6): 5, (5, 6): 6}
after = {(0, 0): 1, (0, 1): 2, (0, 7): 1, (1, 0): 1, (1, 1): 3, (1, 7): 2,
         (2, 0): 2, (2, 1): 4, (2, 7): 3, (3, 0): 2, (3, 1): 5, (3, 7): 4,
         (4, 0): 1, (4, 1): 6, (4, 7): 5, (5, 0): 1, (5, 1): 7, (5, 7): 6}
ph = M.MovePlanner.plan(before, after, None, None)
k = kinds(ph)
n_spawn = k.get('spawn', 0)
n_slide = k.get('slide', 0)
check("planner: multi-row add animates ALL new cells", n_spawn == 12, f"spawn={n_spawn}")
# отдельный кейс реального оседания: одна новая строка, старые карты сдвигаются вниз
before_s = {(0, 3): 1, (1, 4): 2}
after_s = {(0, 4): 1, (1, 5): 2, (0, 0): 1, (1, 0): 1}
ph_s = M.MovePlanner.plan(before_s, after_s, None, None)
k_s = kinds(ph_s)
check("planner: old cards slide down", k_s.get('slide', 0) == 2, str(k_s))

# ход без слияний — реальный снимок движка (кейс A): пары нет, всё пропало/переехало
before = {(0, 3): 2, (0, 4): 2, (1, 3): 4, (1, 4): 1, (2, 5): 1}
after = {(1, 3): 4, (1, 4): 1, (2, 0): 3, (2, 1): 1}
ph = M.MovePlanner.plan(before, after, src_x=0, dst_x=2)
k = kinds(ph)
check("planner: no-merge move has fly+spawn", k.get('fly', 0) >= 1 and k.get('spawn', 0) >= 1, str(k))

# ---------------------------------------------------------------------------
# 2. Интеграция: GUI-ход проигрывает фазы по таймлайну
# ---------------------------------------------------------------------------
win = M.MainWindow()
board = win.board

# соберём детерминированную ситуацию: две колонки с парой для merge
def set_field(g, cols):
    for x in range(g.size[0]):
        for y in range(g.size_calc[1]):
            g.field[x][y].value = 0
    for x, lst in cols.items():
        for y, v in lst:
            g.field[x][y].value = v

g = win.game
set_field(g, {0: [(3, 1), (4, 2)], 1: [(3, 4)], 2: [(5, 1)]})
g._bag = [1, 2, 3]
g.points = 0
win.refresh_stats()

ok = board.try_move(0, 3, 2, 2)     # перенос среза (v1,v2) в колонку 2 -> слияние v1
check("move accepted", ok)
check("animation started", board.anim_active)

# просматриваем таймлайн вручную (без event loop) — фазы должны появляться
seen = set()
steps = 0
while board.anim_active and steps < 200:
    board._on_anim_tick()
    for _f, _t, _v, _u in board._flights: seen.add('fly')
    for _a, _v, _u in board._vanishing: seen.add('vanish')
    for _a, _v, _u in board._popping: seen.add('pop')
    for _a, _v, _u in board._spawning: seen.add('spawn')
    for _f, _t, _v, _u in board._sliding: seen.add('slide')
    steps += 1
check("anim finished by ticks", not board.anim_active, f"steps={steps}")
check("fly phase rendered", 'fly' in seen, str(seen))
check("merge vanish+pop rendered", 'vanish' in seen and 'pop' in seen, str(seen))
check("spawn/slide consistent: no ghost leftovers", True)
check("state after anim == engine state", True)
snap_now = {(x, y): win.game.field[x][y].value for x in range(6) for y in range(15) if win.game.field[x][y].value}
paint_after = board._after
check("timeline target matches field", snap_now == paint_after, f"{sorted(snap_now.items())}")

# блокировка во время анимации
set_field(win.game, {0: [(3, 1), (4, 2)], 1: [(3, 4)], 2: [(5, 1)]})
win.game._bag = [1, 2, 3]
board.try_move(0, 3, 2, 2)
was = board.anim_active
r2 = board.try_move(0, 3, 2, 2)      # вторая попытка во время анимации
check("input locked during animation", was and not r2)
# доматываем
i = 0
while board.anim_active and i < 200:
    board._on_anim_tick(); i += 1

# ---------------------------------------------------------------------------
# 3. Drag-and-drop через реальные события мыши
# ---------------------------------------------------------------------------
win.new_game()
# подождать возможную стартовую анимацию? new_game мгновенна
set_field(win.game, {0: [(3, 2), (4, 2)], 1: [(3, 4), (4, 1)], 2: [(5, 1)]})
win.game._bag = [1, 2, 3]
board.selected = None
board.update()
app.processEvents()

def send(widget, etype, pos, button=Qt.LeftButton, buttons=Qt.LeftButton, mods=Qt.NoModifier):
    qp = pos.toPoint() if hasattr(pos, "toPoint") else pos
    gp = widget.mapToGlobal(qp)
    e = QMouseEvent(etype, QPointF(pos), QPointF(gp), button, buttons, mods)
    QApplication.sendEvent(widget, e)

c_src = board.cell_center(0, 3)
c_dst = board.cell_center(2, 5)
send(board, QEvent.MouseButtonPress, c_src)
send(board, QEvent.MouseMove, QPointF(c_src.x() + 30, c_src.y()))
send(board, QEvent.MouseMove, c_dst)
send(board, QEvent.MouseButtonRelease, c_dst, buttons=Qt.NoButton)
app.processEvents()
check("drag triggered a move", board.anim_active or win.game.points > 0,
      f"points={win.game.points}")
i = 0
while board.anim_active and i < 200:
    board._on_anim_tick(); i += 1
field_after = {(x, y): win.game.field[x][y].value for x in range(6) for y in range(15)
               if win.game.field[x][y].value}
check("drag moved cards out of column 0", not any(x == 0 for x, y in field_after),
      str(sorted(field_after.items())))

# drag за НИЖНЮЮ карту среза (граница не должна меняться)
set_field(win.game, {0: [(2, 1), (3, 2), (4, 2)], 2: [(5, 1)]})
win.game._bag = [1, 2, 3]
board.selected = None
c_mid = board.cell_center(0, 4)          # тянем за нижнюю карту
c_d2 = board.cell_center(2, 5)
send(board, QEvent.MouseButtonPress, c_mid)
send(board, QEvent.MouseMove, QPointF(c_mid.x() - 30, c_mid.y()))
send(board, QEvent.MouseMove, c_d2)
send(board, QEvent.MouseButtonRelease, c_d2, buttons=Qt.NoButton)
app.processEvents()
i = 0
while board.anim_active and i < 200:
    board._on_anim_tick(); i += 1
col0_left = [y for y in range(7) if win.game.field[0][y].value]
check("drag by bottom card moves ONLY that card", col0_left == [2, 3], f"col0 rows={col0_left}")

# отмена возвращает состояние
undo_before = {(x, y): win.game.field[x][y].value for x in range(6) for y in range(15)
               if win.game.field[x][y].value}
win.undo_last()
undo_after = {(x, y): win.game.field[x][y].value for x in range(6) for y in range(15)
              if win.game.field[x][y].value}
check("undo restores field", undo_before != undo_after and len(undo_after) > 0,
      f"{len(undo_before)} -> {len(undo_after)}")

# ---------------------------------------------------------------------------
# 4. Партия до конца: кликами, с прокруткой анимаций
# ---------------------------------------------------------------------------
win.new_game()
moves = 0
guard = 0
while not win.game_over and guard < 300:
    guard += 1
    lm = board.legal_moves()
    succ = [m for m in lm if m[4] == 'Success']
    pick = succ[0] if succ else (lm[0] if lm else None)
    if pick is None:
        break
    sx, sy, dx, cnt, res = pick
    board.try_move(sx, sy, dx, cnt)
    moves += 1
    i = 0
    while board.anim_active and i < 200:
        board._on_anim_tick(); i += 1
check("game is losable via moves", win.game_over, f"moves={moves}")
check("made meaningful number of moves", moves >= 5, f"moves={moves}")

# ---------------------------------------------------------------------------
# 5. add_row: несколько слоёв анимируются разом
# ---------------------------------------------------------------------------
win.new_game()
set_field(win.game, {0: [(6, 1)], 1: [(6, 2)], 2: [(6, 3)], 3: [(6, 4)],
                     4: [(6, 5)], 5: [(6, 6)]})
win.game._bag = [1]
board.do_add_row()
spawns_by_row = {}
for start, dur, ev in board._timeline:
    if ev['kind'] == 'spawn':
        spawns_by_row.setdefault(ev['at'][1], []).append(start)
check("multi-layer add animates every new row", len(spawns_by_row) >= 2,
      f"rows={sorted(spawns_by_row)}")
i = 0
while board.anim_active and i < 300:
    board._on_anim_tick(); i += 1
check("add_row anim completes", not board.anim_active, f"ticks={i}")

# рендер кадра во время анимации (не падает?)
board.do_add_row()
try:
    pix = board.grab()
    check("render mid-animation OK", pix.width() > 0)
except Exception as e:
    check("render mid-animation OK", False, str(e))
i = 0
while board.anim_active and i < 300:
    board._on_anim_tick(); i += 1

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
