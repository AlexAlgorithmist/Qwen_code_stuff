# -*- coding: utf-8 -*-
"""Инструмент для захвата РЕАЛЬНЫХ событий движка (API.py) во время хода.

Используется GUI (main.py) для покадровой анимации: вместо эвристического
сравнения снимков «до/после» мы временно подменяем методы Game и записываем
каждое каскадное смещение, слияние, схлопывание и доброс строк ровно в том
порядке, в котором их исполняет сам движок. Ничего в API не меняется —
подмена живёт только на время одного вызова и всегда восстанавливается.
"""


class EventRecorder:
    """Обёртка над объектом Game; пишет события в self.events."""

    def __init__(self, game):
        self.g = game
        self.S1 = game.size[1]
        self.H = game.size_calc[1]
        self.events = []
        self._saved = {}

    # --- запись -----------------------------------------------------------
    def rec(self, op, **kw):
        kw['op'] = op
        self.events.append(kw)

    # --- перехват low-level примитивов ------------------------------------
    def _wrap_addrow(self):
        g = self.g
        orig = g._addrow

        def wrapped(need=None, count_rows=1):
            snap = {x: [g.field[x][y].value for y in range(self.H)]
                    for x in range(g.size_calc[0])}
            ret = orig(need=need, count_rows=count_rows)
            if ret is False:
                return ret
            for x in range(g.size_calc[0]):
                moved = []
                for y in range(self.H):
                    v = snap[x][y]
                    if v and g.field[x][y + count_rows].value == v \
                            and y + count_rows < self.H:
                        moved.append((y, v))
                for y, v in sorted(moved, reverse=True):
                    self.rec('slide', frm=(x, y), to=(x, y + count_rows), v=v)
                for y in range(count_rows):
                    nv = g.field[x][y].value
                    if nv:
                        self.rec('spawn', at=(x, y), v=nv, row=y)
            return ret
        return wrapped

    def _wrap_merge(self):
        g = self.g
        orig = g._merge

        def wrapped(column):
            merged = False
            # В ТОЧНОСТИ повторяем цикл Game._merge (API.py), записывая
            # каждое слияние как пару событий vanish+pop.
            for y in range(self.H - 2, 0, -1):
                if g.field[column][y].value == 0:
                    continue
                if g.field[column][y].value == g.field[column][y + 1].value:
                    g._combo += 1
                    g.points += (2 << g.field[column][y].value) * g._combo
                    nv = g.field[column][y].value + 1
                    g.field[column][y].value = nv
                    g.field[column][y + 1].value = 0
                    self.rec('vanish', at=(column, y + 1), v=nv - 1)
                    self.rec('pop', at=(column, y), v=nv)
                    merged = True
                    continue
                if g.field[column][y - 1].value == g.field[column][y].value:
                    g._combo += 1
                    g.points += (2 << g.field[column][y - 1].value) * g._combo
                    nv = g.field[column][y - 1].value + 1
                    g.field[column][y - 1].value = nv
                    g.field[column][y].value = 0
                    self.rec('vanish', at=(column, y), v=nv - 1)
                    self.rec('pop', at=(column, y - 1), v=nv)
                    merged = True
                    continue
            g.bestCombo = max(g.bestCombo, g._combo)
            return merged
        return wrapped

    def _wrap_compress(self):
        g = self.g
        orig = g._compress

        def wrapped(column):
            did = False
            while True:
                mv = None
                for y in range(self.H - 1, 1, -1):
                    if g.field[column][y - 1].value == 0 \
                            and g.field[column][y].value != 0:
                        mv = y
                        break
                if mv is None:
                    break
                v = g.field[column][mv].value
                g.field[column][mv - 1].value = v
                g.field[column][mv].value = 0
                self.rec('slide', frm=(column, mv), to=(column, mv - 1), v=v)
                did = True
            return did
        return wrapped

    # --- перехват публичных методов (для отсечения скрытых рядов) ---------
    def _wrap_action_full(self):
        g = self.g
        orig = g.action_full

        def wrapped(posFrom, posTo, count=1):
            n_before = len(self.events)
            res = orig(posFrom, posTo, count=count)
            self._trim(n_before)
            return res
        return wrapped

    def _wrap_add_row(self):
        g = self.g
        orig = g.add_row

        def wrapped(need=None, count_rows=1):
            n_before = len(self.events)
            res = orig(need=need, count_rows=count_rows)
            self._trim(n_before)
            return res
        return wrapped

    def _trim(self, n_before):
        """Убирает из хвоста события карт, уехавших за нижний край поля."""
        keep = []
        for ev in self.events[n_before:]:
            pts = []
            if 'frm' in ev:
                pts.append(ev['frm'])
            if 'to' in ev:
                pts.append(ev['to'])
            if 'at' in ev:
                pts.append(ev['at'])
            if any(y >= self.S1 for (_, y) in pts):
                continue
            keep.append(ev)
        del self.events[n_before:]
        self.events.extend(keep)

    # --- жизненный цикл ----------------------------------------------------
    def __enter__(self):
        g = self.g
        self._saved = {'_addrow': g._addrow, '_merge': g._merge,
                       '_compress': g._compress,
                       'action_full': g.action_full, 'add_row': g.add_row}
        g._addrow = self._wrap_addrow()
        g._merge = self._wrap_merge()
        g._compress = self._wrap_compress()
        g.action_full = self._wrap_action_full()
        g.add_row = self._wrap_add_row()
        return self

    def __exit__(self, *exc):
        g = self.g
        for name, fn in self._saved.items():
            setattr(g, name, fn)
        self._saved = {}
        return False
