# -*- coding: utf-8 -*-
"""Headless-диагностика анимаций и drag-and-drop в main.py."""
import os, sys, random
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mergeCards"))
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtWidgets import QApplication
from API import Game
import main as M

app = QApplication(sys.argv)

def snap(g):
    return {(x,y): g.field[x][y].value for x in range(6) for y in range(g.size_calc[1]) if g.field[x][y].value}

def run_ticks(b, n):
    frames=[]
    for _ in range(n):
        b._on_anim_tick()
        frames.append(dict(fly=len(b._flights),van=len(b._vanishing),pop=len(b._popping),
                           sl=len(b._sliding),sp=len(b._spawning)))
    return frames

fails = []
random.seed(7)
win = M.MainWindow(); win.show()
bd = win.board

print("=== Стресс: 10 партий, все ходы ===")
total_moves = sim_ok = diff_used = no_fly = added_no_spawn = merge_no_pop = merges = 0
for game_i in range(10):
    win.new_game()
    for step in range(60):
        if win.game_over or bd.anim_active: break
        ms=[m for m in bd.legal_moves() if m[4]=="Success"]
        if not ms: break
        sx,sy,dx,cnt,_=random.choice(ms)
        before=snap(bd.game)
        gc=bd._clone()
        r,added=gc.action_full((sx,sy),dx,count=cnt)
        after_gt=snap(gc)
        ok=bd.try_move(sx,sy,dx,cnt)
        if not ok: continue
        total_moves+=1
        after=snap(bd.game)
        # what planner used?
        st = M.MovePlanner.simulate_move(before, sx, dx, after) if False else None
        steps_sim = M.MovePlanner.simulate_move(before, sx, dx, after)
        if steps_sim is None:
            diff_used += 1
        else:
            sim_ok += 1
        kinds={k:0 for k in ("fly","van","pop","sl","sp")}
        fr=run_ticks(bd,200)
        for f in fr:
            for k in kinds: kinds[k]=max(kinds[k],f[k])
        if kinds['fly']==0:
            no_fly+=1
            if len(fails)<5: fails.append(("NO FLY",(sx,sy,dx,cnt),before,after))
        if added and kinds['sp']==0:
            added_no_spawn+=1
            if len(fails)<8: fails.append(("ADDED but NO SPAWN",(sx,sy,dx,cnt),before,after))
        # detect merges: value disappeared that had pair partner -> use points jump heuristic: lastCombo
        if max(bd.game.lastCombo)>0:
            merges+=1
            if kinds['pop']==0 or kinds['van']==0:
                merge_no_pop+=1
                if len(fails)<10: fails.append(("MERGE but no pop/van",(sx,sy,dx,cnt),kinds,before,after))
print(dict(total=total_moves, sim=sim_ok, diff=diff_used, no_fly=no_fly,
           added_no_spawn=added_no_spawn, merges=merges, merge_no_pop=merge_no_pop))
for f in fails[:6]: print("FAIL:",f)

print("\n=== add_row со множественным добросом ===")
g3 = Game((6,7), {})
for x,v in enumerate([1,2,4,8,16,32]): g3.field[x][0].value=v
g3.add_row()
win.new_game(); win.game=g3; win.board.game=g3
before=snap(g3)
probe=win.board.clone_or_self(); probe.add_row()
after=snap(probe)
new_rows=sorted({y for (x,y) in after if (x,y) not in before and y<7})
steps=M.MovePlanner._diff_steps(before,after)
spawns=[s for s in steps if s['op']=='spawn']
slides=[s for s in steps if s['op']=='slide']
print("rows added:",new_rows,"spawn events:",len(spawns),"slide events:",len(slides))
assert len(spawns)>=6*len(new_rows)-6, "spawn не покрывает все новые строки!"

print("\n=== Drag-and-drop жестом ===")
win.new_game()
b4=win.board; b4.resize(b4.minimumSize())
cols=[x for x in range(6) if b4.visible_cards(x)]
sx,tx=cols[0],cols[-1]
sy=b4.visible_cards(sx)[0]
p1=b4.cell_center(sx,sy); p2=b4.cell_center(tx,sy)
from PyQt5.QtGui import QMouseEvent
def me(t,pos,btns,mods=Qt.NoModifier): return QMouseEvent(t,pos,Qt.LeftButton,btns,mods)
app.sendEvent(b4, me(QMouseEvent.MouseButtonPress,QPointF(p1),Qt.LeftButton))
app.sendEvent(b4, me(QMouseEvent.MouseMove,QPointF((p1.x()+p2.x())/2,p1.y()),Qt.LeftButton))
drag_mid = b4.dragging
# ghost visible during drag? grab() renders paintEvent with dragging=True
pix = b4.grab()
app.sendEvent(b4, me(QMouseEvent.MouseMove,QPointF(p2),Qt.LeftButton))
app.sendEvent(b4, me(QMouseEvent.MouseButtonRelease,QPointF(p2),Qt.NoButton))
print("drag started:",drag_mid,"| move accepted:",b4.anim_active)
fr=run_ticks(b4,200)
kinds={k:max(f[k] for f in fr) for k in ("fly","van","pop","sl","sp")}
print("anim kinds:",kinds)
b4.grab()
print("OK — приложение живо:", True)

print("\n=== Клик-ход тоже работает ===")
win.new_game()
b5=win.board
c=[x for x in range(6) if b5.visible_cards(x)]
app.sendEvent(b5, me(QMouseEvent.MouseButtonPress,QPointF(b5.cell_center(c[0],b5.visible_cards(c[0])[0])),Qt.LeftButton))
app.sendEvent(b5, me(QMouseEvent.MouseButtonRelease,QPointF(b5.cell_center(c[0],b5.visible_cards(c[0])[0])),Qt.NoButton))
app.sendEvent(b5, me(QMouseEvent.MouseButtonPress,QPointF(b5.cell_center(c[1],0)),Qt.LeftButton))
app.sendEvent(b5, me(QMouseEvent.MouseButtonRelease,QPointF(b5.cell_center(c[1],0)),Qt.NoButton))
print("click-move anim:",b5.anim_active)
run_ticks(b5,200)
print("done")
