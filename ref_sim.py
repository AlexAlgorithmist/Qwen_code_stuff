# -*- coding: utf-8 -*-
"""Эталонный симулятор механики mergeCards/API.py с журналом событий.

Повторяет Game (action_full/add_row/merge/_merge/_compress/_addrow)
клетка-в-клетку, но на собственной сетке list[list[int]]; в self.ev пишет
каждое событие только для ВИДИМЫХ клеток (y < H):
  ('fly', frm, to, v)     — серия перенесена в другую колонку (на её верх)
  ('vanish', at, v)       — карта вита в результат слияния
  ('merge', at, nv)       — рождение результата слияния
  ('slide', frm, to, v)   — переезд (compress вниз / каскад доброса вниз)
  ('fadeout', at, v)      — карта ушла за верхний край при каскаде
  ('spawn', at, v)        — карта появилась из новой строки

Координаты движка: y=0 — ВЕРХНИЙ видимый ряд; новые строки появляются сверху
и сдвигают всё вниз; буферные клетки за краем — y в [H, size_calc[1]).
"""

W, H = 6, 7
FULL_H = 2 * H + 1          # size_calc[1] движка


class RefSim:
    def __init__(self, snap=None, grid=None):
        if grid is not None:
            # прямое копирование сетки движка (включая буферные ряды)
            self.g = [list(col) for col in grid]
        else:
            self.g = [[0] * FULL_H for _ in range(W)]
            if snap:
                for (x, y), v in snap.items():
                    if v and 0 <= x < W and 0 <= y < FULL_H:
                        self.g[x][y] = v
        self.ev = []

    def field(self):
        return {(x, y): self.g[x][y]
                for x in range(W) for y in range(FULL_H) if self.g[x][y]}

    def vis_field(self):
        return {(x, y): self.g[x][y]
                for x in range(W) for y in range(H) if self.g[x][y]}

    # ---- примитивы -------------------------------------------------------
    def top_full(self):
        return any(self.g[x][H] for x in range(W))

    def _merge_col(self, x):
        """Точно один проход Game._merge(x)."""
        g = self.g[x]
        for y in range(FULL_H - 2, 0, -1):
            if g[y] == 0:
                continue
            if g[y] == g[y + 1]:
                nv = g[y] + 1
                g[y] = nv
                g[y + 1] = 0
                if y + 1 < H:
                    self.ev.append(('vanish', (x, y + 1)))
                if y < H:
                    self.ev.append(('merge', (x, y), nv))
                return True
            if g[y - 1] == g[y]:
                nv = g[y - 1] + 1
                g[y - 1] = nv
                g[y] = 0
                if y < H:
                    self.ev.append(('vanish', (x, y)))
                if y - 1 < H:
                    self.ev.append(('merge', (x, y - 1), nv))
                return True
        return False

    def _compress_col(self, x):
        """Один проход Game._compress(x): карты падают вниз (к меньшим y)."""
        g = self.g[x]
        moved = False
        for y in range(FULL_H - 1, 1, -1):
            if g[y - 1] == 0 and g[y] != 0:
                if y < H:
                    self.ev.append(('slide', (x, y), (x, y - 1), g[y]))
                g[y - 1] = g[y]
                g[y] = 0
                moved = True
        return moved

    def merge_all(self):
        merged = False
        for x in range(W):
            while self._merge_col(x):
                merged = True
                while self._compress_col(x):
                    pass
        return merged

    def can_merge(self):
        were = set()
        for x in range(W):
            for y in range(FULL_H):
                v = self.g[x][y]
                if v > 0:
                    if v in were:
                        return True
                    were.add(v)
        return False

    def addrow(self, new_row_vals, count_rows=1):
        """Game._addrow(count_rows): всё съезжает на count_rows вниз,
        верхние count_rows рядов заполняются значениями new_row_vals."""
        for x in range(W):
            for y in range(FULL_H - count_rows - 1, -1, -1):
                if self.g[x][y] == 0:
                    continue
                ny = y + count_rows
                if ny >= FULL_H:
                    return False
                self.g[x][ny] = self.g[x][y]
        # журнал каскада: снизу вверх; y+count_rows>=H уходит в буфер — гаснет
        for y in range(FULL_H - count_rows - 1, -1, -1):
            for x in range(W):
                v = self.g[x][y]
                if v == 0:
                    continue
                if y + count_rows < H:
                    self.ev.append(('slide', (x, y), (x, y + count_rows), v))
                elif y < H:
                    self.ev.append(('fadeout', (x, y), v))
        for x in range(W):
            for yy in range(count_rows):
                v = (new_row_vals.get(yy) or {}).get(x, 0) or 0
                self.g[x][yy] = v
                if v:
                    self.ev.append(('spawn', (x, yy), v))
        return True

    # ---- ход action_full ---------------------------------------------------
    def move(self, sx, sy, dx, cnt, row_fn):
        g = self.g
        if self.top_full():
            return 'invalid_top'
        if sx == dx or not (0 <= sx < W and 0 <= sy < H and 0 <= dx < W):
            return 'invalid_pos'
        if g[sx][sy] == 0:
            return 'invalid_pos'
        cards = []
        for y in range(sy, H):
            if g[sx][y] == 0:
                break
            cards.append(g[sx][y])
            g[sx][y] = 0
        if len(cards) != cnt:
            return 'invalid_count'
        # yStart как в движке: последний непустой по ВСЕМУ столбцу цели
        yStart = -1
        for y in range(FULL_H):
            if g[dx][y] == 0:
                break
            yStart = y
        for i, v in enumerate(cards):
            ty = yStart + 1 + i
            if ty >= FULL_H:
                return 'invalid_count'
            g[dx][ty] = v
            if ty < H:
                self.ev.append(('fly', (sx, sy + i), (dx, ty), v))
        merged = self.merge_all()
        if self.top_full():
            return 'game_over'
        if not merged:
            if not self.addrow(row_fn(), count_rows=cnt):
                return 'game_over'
            self.merge_all()
            if self.top_full():
                return 'game_over'
        while not self.can_merge():
            if not self.addrow(row_fn(), count_rows=cnt):
                return 'game_over'
            self.merge_all()
            if self.top_full():
                return 'game_over'
        return 'success'

    # ---- add_row -----------------------------------------------------------
    def do_add_row(self, row_fn, count_rows=1):
        if not self.addrow(row_fn(), count_rows=count_rows):
            return 'game_over'
        self.merge_all()
        if self.top_full():
            return 'game_over'
        while not self.can_merge():
            if not self.addrow(row_fn(), count_rows=count_rows):
                return 'game_over'
            self.merge_all()
            if self.top_full():
                return 'game_over'
        return 'success'
