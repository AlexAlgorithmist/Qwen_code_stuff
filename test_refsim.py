# -*- coding: utf-8 -*-
"""Парити RefSim с голым движком API на случайных партиях.

Каждый ход движка прогоняется ДВАЖДЫ с одним seed глобального random —
ход детерминирован, поэтому второй прогон даёт те же самые добавленные
строки. Шпион во втором прогоне ловит состояние СРАЗУ после cascade+fill
(значения новых рядов до merge); их скармливаем RefSim и сверяем
финальное поле (все ряды, включая буфер) и статус хода."""
import random, sys
sys.path.insert(0, "mergeCards")
from API import Game, Card
from ref_sim import RefSim, W, H, FULL_H


def snap(g):
    return {(x, y): g.field[x][y].value
            for x in range(W) for y in range(FULL_H) if g.field[x][y].value}


def grid_of(g):
    return [[g.field[x][y].value for y in range(FULL_H)] for x in range(W)]


class Spy(Game):
    rows = None

    def _addrow(self, need=None, count_rows=1):
        # даём randint отработать, но ПОДСМАТРИВАЕМ новый ряд до fill:
        # значения ряда = то, что вернул бы bag; проще всего — вызвать
        # оригинал, а затем восстановить «чистые» значения из after-поля?
        # Нет: после fill идёт merge внутри _addrow? Нет! В движке merge
        # вызывается снаружи (_addrow только сдвигает и заполняет).
        r = Game._addrow(self, need=need, count_rows=count_rows)
        if r:
            self.rows.append(
                {yy: {x: self.field[x][yy].value for x in range(self.size[0])}
                 for yy in range(count_rows)})
        return r


def run_plain(before, fn):
    gc = Game(before.size, before.field, points=before.points,
              bestCombo=before.bestCombo, _bag=list(before._bag))
    res = fn(gc)
    return res, gc


def run_spy(before, fn):
    gc = Spy(before.size, before.field, points=before.points,
             bestCombo=before.bestCombo, _bag=list(before._bag))
    gc.rows = []
    res = fn(gc)
    return res, gc, list(gc.rows)


fails = 0
trials = 0
for seed0 in range(300):
    rng = random.Random(seed0)
    random.seed(seed0)
    g = Game((W, H), {})
    g.add_row()
    rs = RefSim(grid=grid_of(g))
    assert rs.field() == snap(g), f"seed {seed0}: init mismatch"
    over = False
    for step in range(60):
        if over:
            break
        occ = {x: [y for y in range(H) if g.field[x][y].value] for x in range(W)}
        cand = []
        for sx in range(W):
            ys = occ[sx]
            for a in range(len(ys)):
                for b in range(a, len(ys)):
                    if ys[b] != ys[a] + (b - a):
                        break
                    for dx in range(W):
                        if dx != sx:
                            cand.append((sx, ys[a], dx, b - a + 1))
        use_addrow = rng.random() < 0.25 or not cand
        sd = rng.randrange(10 ** 9)
        # снимок before ДО любого прогона (движок мутирует field на месте!)
        grid_before = [[g.field[x][y].value for y in range(FULL_H)]
                       for x in range(W)]
        snap_before = {(x, y): v for x in range(W) for y in range(FULL_H)
                       for v in [grid_before[x][y]] if v}
        if use_addrow:
            fn = lambda gg: gg.add_row()
            mover = None
        else:
            sx, sy, dx, cnt = rng.choice(cand)
            fn = lambda gg: gg.action_full((sx, sy), dx, count=cnt)
            mover = (sx, sy, dx, cnt)
        random.seed(sd)
        res1, gc1 = run_plain(g, fn)
        g = Game(g.size, grid_to_field(grid_before), points=g.points,
                 bestCombo=g.bestCombo, _bag=list(g._bag))
        random.seed(sd)
        res2, gc2, rows = run_spy(g, fn)
        s1 = 'Game Over' if str(res1).startswith('Game Over') else str(res1)
        s2 = 'Game Over' if str(res2).startswith('Game Over') else str(res2)
        if (s1.startswith('Game Over') != s2.startswith('Game Over')) \
                or (s1 == 'Success') != (s2 == 'Success') \
                or snap(gc1) != snap(gc2):
            print(f"SPY-DIVERGE seed={seed0} step={step} {res1!r} vs {res2!r}")
            fails += 1
            if fails >= 5:
                sys.exit(1)
            g = gc1
            continue
        rr = iter(rows)
        rs2 = RefSim(grid=grid_of(g))
        if mover:
            sx, sy, dx, cnt = mover
            rres = rs2.move(sx, sy, dx, cnt, lambda: next(rr, {}))
            status_match = (s1 == 'Success' and rres == 'success') or \
                           (s1.startswith('Game Over') and rres == 'game_over')
        else:
            rres = rs2.do_add_row(lambda: next(rr, {}))
            status_match = rres in ('success', 'game_over')
        trials += 1
        field_match = rs2.field() == snap(gc1)
        if not (status_match and field_match):
            fails += 1
            print(f"MISMATCH seed={seed0} step={step} move={mover} "
                  f"engine={res1!r} sim={rres!r} rows={rows}")
            only_e = {k: v for k, v in snap(gc1).items()
                      if rs2.field().get(k) != v}
            only_s = {k: v for k, v in rs2.field().items()
                      if snap(gc1).get(k) != v}
            print("  engine-only:", sorted(only_e.items()))
            print("  sim-only   :", sorted(only_s.items()))
            if fails >= 5:
                sys.exit(1)
        if s1.startswith('Game Over'):
            over = True
        g = gc1
print(f"trials={trials} fails={fails}")
