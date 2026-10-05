from random import randint
from math import isinf


def pretty_number(number, pres=2, exact=False, overflow=4, overflow_pres=6):
    if type(number) == float:
        if isinf(number):
            return str(number)
        num = f"{number:.{pres}f}"
    elif type(number) == int:
        num = str(number)
    else:
        return number
    res = num.split(".")
    if not exact and len(res[0]) > overflow * 3:
        count = (len(res[0]) - 1) // 3
        part = int(res[0][:len(res[0]) - count*3 + overflow_pres + 1])
        return f"{part / 10 ** (overflow_pres + 1):.{overflow_pres}f} * 10^{count*3}"
    return ".".join(["'".join(res[0][::-1][i:i+3] for i in range(0, len(res[0]), 3))[::-1]] + res[1:])


class Card:
    def __init__(self, pos: tuple[int, int], value: int):
        self.pos = pos
        self.value = value

    def __str__(self):
        return str(int(1 << self.value)) if self.value > 0 else ""
    def __repr__(self):
        return f"Card({self.pos}, {self.value})"

    def copy(self):
        return Card((self.pos[0], self.pos[1]), self.value)


class Game:
    def __init__(self, size: tuple[int, int], cards: dict[int: dict[int: Card]], *, points: int = 0, bestCombo: int = 0, _bag: list[int] = None) -> None:
        if _bag is None:
            _bag = []
        self.size = size
        self.size_calc = (size[0], size[1]*2 + 1)
        self.field: dict[int: dict[int: Card]] = {}
        for x in range(self.size_calc[0]):
            self.field[x] = {}
            for y in range(self.size_calc[1]):
                card = Card((x, y), 0)
                if x in cards.keys():
                    if y in cards[x].keys():
                        card = cards[x][y].copy()
                self.field[x][y] = card
        self.points = points
        self._combo = 0
        self.bestCombo = bestCombo
        self._bag = _bag
        self.lastCombo = []

    def action_full(self, posFrom: tuple[int, int], posTo: int, *, count: int = 1):
        self.lastCombo = []
        added_row = False
        for x in range(self.size_calc[0]):
            if self.field[x][self.size[1]].value:
                return "Invalid position", added_row
        if posFrom[0] == posTo or posFrom[0] > self.size[0] or posFrom[0] < 0 or posFrom[1] > self.size[1] or posFrom[1] < 0 or posTo > self.size[0] or posTo < 0:
            return "Invalid position", added_row
        if self.field[posFrom[0]][posFrom[1]].value == 0:
            return "Invalid position", added_row
        cards: list[Card] = []
        for y in range(posFrom[1], self.size[1]):
            if self.field[posFrom[0]][y].value == 0:
                break
            cards.append(self.field[posFrom[0]][y].value)
            self.field[posFrom[0]][y].value = 0
        if not cards:
            return "Invalid position", added_row
        yStart = -1
        for y in range(self.size_calc[1]):
            if self.field[posTo][y].value == 0:
                break
            yStart = y
        # try:
        for i in range(len(cards)):
            self.field[posTo][i+yStart+1].value = cards[i]
        # except Exception as e:
        #     print(i)
        #     print(self)
        #     print(cards)
        #     print(posFrom, posTo)
        #     for i in range(self.size_calc[1]):
        #         print("   ", self.field[posTo][i].value)
        #     exit(e)
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

    def _merge(self, column):
        merged = False
        for y in range(self.size_calc[1] - 2, 0, -1):
            if self.field[column][y].value == 0:
                continue

            if self.field[column][y].value == self.field[column][y + 1].value:
                self._combo += 1
                self.points += (2 << self.field[column][y].value) * self._combo
                self.field[column][y].value += 1
                self.field[column][y + 1].value = 0
                merged = True
                continue
            if self.field[column][y - 1].value == self.field[column][y].value:
                self._combo += 1
                self.points += (2 << self.field[column][y - 1].value) * self._combo
                self.field[column][y - 1].value += 1
                self.field[column][y].value = 0
                merged = True
                continue
        self.bestCombo = max(self.bestCombo, self._combo)
        return merged

    def _compress(self, column):
        compressed = False
        for y in range(self.size_calc[1]-1, 1, -1):
            if self.field[column][y-1].value == 0 and self.field[column][y].value != 0:
                self.field[column][y-1].value = self.field[column][y].value
                self.field[column][y].value = 0
                compressed = True
        return compressed

    def _findGap(self):
        minVal = 1000000
        maxVal = 0
        for x in range(self.size_calc[0]):
            for y in range(self.size_calc[1]):
                value = self.field[x][y].value
                if value == 0:
                    continue
                minVal = min(minVal, value)
                maxVal = max(maxVal, value)
        return minVal, maxVal

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
            self._bag = list(range(maxVal - 10 if maxVal > 10 else 1, maxVal - 3 if maxVal > 3 else 1))
            if not self._bag:
                self._bag = [1]
        if need:
            self._bag.extend(i for i in need if i not in self._bag)
        for x in range(self.size_calc[0]):
            for y in range(self.size_calc[1]-count_rows-1, -1, -1):
                if self.field[x][y].value == 0:
                    continue
                if y + count_rows > self.size_calc[1]:
                    return False
                self.field[x][y+count_rows].value = self.field[x][y].value
        for x in range(self.size_calc[0]):
            for y in range(count_rows):
                self.field[x][y].value = self._bag.pop(randint(0, len(self._bag)-1))
                if not self._bag:
                    self._bag = list(range(maxVal - 10 if maxVal > 10 else 1, maxVal - 3 if maxVal > 3 else 1))
                    if not self._bag:
                        self._bag = [1]
        return True

    def _can_merge(self):
        were = set()
        for x in range(self.size_calc[0]):
            for y in range(self.size_calc[1]):
                value = self.field[x][y].value
                if value > 0:
                    if value in were:
                        return True
                    were.add(value)
        return False

    def add_row(self, need=None, count_rows=1):
        if not self._addrow(need=need, count_rows=count_rows):
            return "Game Over: add", True
        for x in range(self.size_calc[0]):
            while self._merge(x):
                while self._compress(x):
                    pass
        for x in range(self.size_calc[0]):
            if self.field[x][self.size[1]].value:
                return "Game Over: much", True
        while not self._can_merge():
            if not self._addrow(count_rows=count_rows):
                return "Game Over: add", True
            for x in range(self.size_calc[0]):
                while self._merge(x):
                    while self._compress(x):
                        pass
            for x in range(self.size_calc[0]):
                if self.field[x][self.size[1]].value:
                    return "Game Over: much", True


    def __str__(self):
        out = f"Points: {pretty_number(self.points, exact=True)}"
        for y in range(self.size[1]):
            if y == self.size[1]:
                out += f"\nv{'v'*(self.size[0])*4}\n|"
            else:
                out += f"\n-{'-'*(self.size[0])*4}\n|"
            for x in range(self.size[0]):
                out += f"{str(self.field[x][y].value): <3}|"
        return out
    def __repr__(self):
        return f"Game({self.size}, {self.field}, points={self.points}, bestCombo={self.bestCombo}, _bag={self._bag})"

    def copy(self):
        return Game(self.size, self.field, points=self.points, bestCombo=self.bestCombo, _bag=self._bag)


if __name__ == "__main__":
    print(pretty_number(1234))
    print(pretty_number(1234.56))
    print(pretty_number(123456))
    print(pretty_number(123456.78))
    print(pretty_number(1234567.8))
    print(pretty_number(1234567890123))
    print(pretty_number(1234567890123.456))
    print(pretty_number(12345678901234.56))
    print(pretty_number(123456789012345.6))
    print(pretty_number(1234567890123456.))
    print(pretty_number(1234567890123456., exact=True))
