# -*- coding: utf-8 -*-
"""Эталонная трассировка движка mergeCards (API.py) на уровне примитивов.

TracingMixin переопределяет _addrow/_merge/_compress/action_full/add_row,
полностью повторяя логику API.Game, но каждое изменение поля порождает
событие в self.events:

    shift(x, frm, to, v)      — карта сдвинута вниз при добавлении строки;
    spawn(x, y, v, rb, batch) — карта вылезла из новой строки (rb — номер
                                блока доброса, y — ряд внутри блока);
    lift(x, y, v, i)          — карта поднята из источника при переносе;
    fly(x, y, dst, dy, v)     — полёт i-й карты переноса в колонку dst;
    merge(x, keep, gone, frm_v, to_v, combo) — схлопывание пары;
    slide(x, frm, to, v)      — compress: карта упала на освободившееся место.

traced_clone(g) строит независимый клон (Game + mixin), прогон действия на
нём даёт события + финальное состояние, идентичное голому Game при том же
random.seed (проверяется тестами). GUI использует события как честный
протокол анимаций: планировать «до/после» эвристиками больше не нужно.
"""

import random


class TracingMixin:
    def ev(self, kind, **kw):
        if not hasattr(self, 'events'):
            self.events = []
        e = dict(kind=kind, **kw)
        self.events.append(e)
        return e

    # ---------------- примитивы (ровно как в API, + события) --------------
    def _addrow(self, need=None, count_rows=1):
        minVal, maxVal = self._findGap()
        if need is None:
            if minVal < maxVal - 10:
                count = 0
                for x in range(self.size_calc[0]):
                    for y in range(self.size_calc[1]):
                        if self.field[x][y].value == minVal:
                            count += 1
                need = count % 2 and [minVal] or []
            else:
                need = []
        if not self._bag:
            self._bag = list(range(maxVal - 10 if maxVal > 10 else 1,
                                   maxVal - 3 if maxVal > 3 else 1))
            if not self._bag:
                self._bag = [1]
        if need:
            self._bag.extend(i for i in need if i not in self._bag)
        H = self.size_calc[1]
        dead = False
        shifts = []
        for x in range(self.size_calc[0]):
            for y in range(H - count_rows - 1, -1, -1):
                if self.field[x][y].value == 0:
                    continue
                if y + count_rows > H:
                    dead = True
                else:
                    shifts.append(dict(kind='shift', x=x, frm=y,
                                       to=y + count_rows,
                                       v=self.field[x][y].value))
                    self.field[x][y + count_rows].value = \
                        self.field[x][y].value
        if dead:
            return False
        self.events.extend(shifts)
        rb = getattr(self, '_rb', -1) + 1
        self._rb = rb
        for x in range(self.size_calc[0]):
            for y in range(count_rows):
                val = self._bag.pop(random.randint(0, len(self._bag) - 1))
                self.field[x][y].value = val
                self.ev('spawn', x=x, y=y, v=val, rb=rb,
                        batch=getattr(self, '_batch', 0))
                if not self._bag:
                    self._bag = list(range(maxVal - 10 if maxVal > 10 else 1,
                                           maxVal - 3 if maxVal > 3 else 1))
                    if not self._bag:
                        self._bag = [1]
        return True

    def _merge(self, column):
        # ВАЖНО: движок начисляет очки ПОСЛЕ инкремента значения, т.е.
        # points += (2 << new_value) * combo — копия этой детали обязательна.
        merged = False
        for y in range(self.size_calc[1] - 2, 0, -1):
            cur = self.field[column][y].value
            if cur == 0:
                continue
            nxt = self.field[column][y + 1].value
            if cur == nxt:
                self._combo += 1
                self.field[column][y].value = cur + 1
                self.points += ((2 << self.field[column][y].value)
                                * self._combo)
                self.field[column][y + 1].value = 0
                self.ev('merge', x=column, keep=y, gone=y + 1,
                        frm_v=cur, to_v=cur + 1, combo=self._combo)
                merged = True
                continue
            prv = self.field[column][y - 1].value
            if prv == cur:
                self._combo += 1
                self.field[column][y - 1].value = cur + 1
                self.points += ((2 << self.field[column][y - 1].value)
                                * self._combo)
                self.field[column][y].value = 0
                self.ev('merge', x=column, keep=y - 1, gone=y,
                        frm_v=cur, to_v=cur + 1, combo=self._combo)
                merged = True
                continue
        self.bestCombo = max(self.bestCombo, self._combo)
        return merged

    def _compress(self, column):
        compressed = False
        for y in range(self.size_calc[1] - 1, 1, -1):
            if (self.field[column][y - 1].value == 0
                    and self.field[column][y].value != 0):
                v = self.field[column][y].value
                self.ev('slide', x=column, frm=y, to=y - 1, v=v)
                self.field[column][y - 1].value = v
                self.field[column][y].value = 0
                compressed = True
        return compressed

    def merge(self, count_idx=None):
        if count_idx is None:
            count_idx = []
        merged = False
        for x in range(self.size_calc[0]):
            self._combo = 0
            while self._merge(x):
                merged = True
                while self._compress(x):
                    pass
            if x in count_idx:
                self.lastCombo.append(self._combo)
        return merged

    # ---------------- действия ---------------------------------------------
    def action_full(self, posFrom, posTo, *, count=1):
        self._batch = getattr(self, '_batch', 0) + 1
        added_row = False
        for x in range(self.size_calc[0]):
            if self.field[x][self.size[1]].value:
                return "Invalid position", added_row
        if (posFrom[0] == posTo or posFrom[0] > self.size[0]
                or posFrom[0] < 0 or posFrom[1] > self.size[1]
                or posFrom[1] < 0 or posTo > self.size[0] or posTo < 0):
            return "Invalid position", added_row
        if self.field[posFrom[0]][posFrom[1]].value == 0:
            return "Invalid position", added_row
        cards = []
        for y in range(posFrom[1], self.size[1]):
            if self.field[posFrom[0]][y].value == 0:
                break
            cards.append(self.field[posFrom[0]][y].value)
        # движок обнуляет источник тем же циклом, что и расставляет;
        # события lift генерим строго параллельно, порядок идентичен
        for i in range(len(cards)):
            self.ev('lift', x=posFrom[0], y=posFrom[1] + i, v=cards[i], i=i)
            self.field[posFrom[0]][i + posFrom[1]].value = 0
        if not cards:
            return "Invalid position", added_row
        yStart = -1
        for y in range(self.size_calc[1]):
            if self.field[posTo][y].value == 0:
                break
            yStart = y
        for i in range(len(cards)):
            self.ev('fly', x=posFrom[0], y=posFrom[1] + i, dst=posTo,
                    dy=i + yStart + 1, v=cards[i])
            self.field[posTo][i + yStart + 1].value = cards[i]
        merged = self.merge([posTo])
        for x in range(self.size_calc[0]):
            if self.field[x][self.size[1]].value:
                return "Game Over: much pre", added_row
        if not merged:
            added_row = True
            if not self._addrow(count_rows=count):
                return "Game Over: add pre", added_row
            self.merge()
            for x in range(self.size_calc[0]):
                if self.field[x][self.size[1]].value:
                    return "Game Over: much mid", added_row
        while not self._can_merge():
            added_row = True
            if not self._addrow(count_rows=count):
                return "Game Over: add aft", added_row
            self.merge()
            for x in range(self.size_calc[0]):
                if self.field[x][self.size[1]].value:
                    return "Game Over: much aft", added_row
        return "Success", added_row

    def add_row(self, need=None, count_rows=1):
        self._batch = getattr(self, '_batch', 0) + 1
        if not self._addrow(need=need, count_rows=count_rows):
            return "Game Over: add", True
        self.merge()
        for x in range(self.size_calc[0]):
            if self.field[x][self.size[1]].value:
                return "Game Over: much", True
        while not self._can_merge():
            if not self._addrow(count_rows=count_rows):
                return "Game Over: add", True
            self.merge()
            for x in range(self.size_calc[0]):
                if self.field[x][self.size[1]].value:
                    return "Game Over: much", True
        return None

    def snapshot(self):
        return {(x, y): self.field[x][y].value
                for x in range(self.size[0])
                for y in range(self.size_calc[1])
                if self.field[x][y].value}


def traced_clone(g):
    """Независимый трассирующий клон игры g (свой _bag, свои Card'ы)."""
    from API import Game

    class TG(TracingMixin, Game):
        pass

    size = g.size
    cards = {}
    for x in range(size[0]):
        for y in range(g.size_calc[1]):
            v = g.field[x][y].value
            if v:
                cards.setdefault(x, {})[y] = g.field[x][y].copy()
    tg = TG(size, cards, points=g.points, bestCombo=g.bestCombo,
            _bag=list(g._bag))
    tg.lastCombo = list(g.lastCombo)
    tg.events = []
    tg._batch = 0
    tg._rb = -1
    return tg


def replay(events, before_snap):
    """Реконструкция поля из снимка «до» и списка событий. Обязана совпасть
    с финальным состоянием движка (проверяется тестами)."""
    rec = dict(before_snap)
    for e in events:
        k = e['kind']
        if k == 'lift':
            pass                              # удаление уже сделал fly
        elif k == 'fly':                       # frm уже снят событием lift
            rec[(e['dst'], e['dy'])] = e['v']
        elif k in ('shift', 'slide'):
            rec.pop((e['x'], e['frm']), None)
            rec[(e['x'], e['to'])] = e['v']
        elif k == 'spawn':
            rec[(e['x'], e['y'])] = e['v']
        elif k == 'merge':
            rec[(e['x'], e['keep'])] = e['to_v']
            rec.pop((e['x'], e['gone']), None)
    return {kk: vv for kk, vv in rec.items() if vv}
