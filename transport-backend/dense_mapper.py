"""DenseMapper — bijection between external codes and contiguous integers.

Used by BusData / TrainData to map ATCO codes, route IDs, and journey
IDs to dense 0-based integers suitable for direct list indexing.

    mapper = DenseMapper()
    idx = mapper.get_int("2500ABC0001")   # 0  (first unseen code)
    idx = mapper.get_int("2500ABC0001")   # 0  (already mapped)
    mapper.get_code(0)                    # "2500ABC0001"
    len(mapper)                           # 1
"""


class DenseMapper:
    """Bi-directional map: external code <-> contiguous int."""

    __slots__ = ("code_to_int", "int_to_code", "next_code")

    def __init__(self):
        self.code_to_int: dict = {}
        self.int_to_code: list = []
        self.next_code: int = 0

    # -- forward: code -> int (auto-assigns on first sight) --------

    def get_int(self, code) -> int:
        """Return the integer for *code*, assigning a new one if unseen."""
        try:
            return self.code_to_int[code]
        except KeyError:
            i = self.next_code
            self.code_to_int[code] = i
            self.int_to_code.append(code)
            self.next_code += 1
            return i

    # -- reverse: int -> code --------------------------------------

    def get_code(self, i: int) -> str:
        """Return the external code for integer *i*."""
        return self.int_to_code[i]

    # -- size -------------------------------------------------------

    def __len__(self) -> int:
        return self.next_code