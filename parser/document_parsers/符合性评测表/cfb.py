from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


FREE_SECTOR = 0xFFFFFFFF
END_OF_CHAIN = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD
DIFAT_SECTOR = 0xFFFFFFFC


class CompoundFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    object_type: int
    start_sector: int
    size: int


class CompoundFile:
    """Small read-only CFB reader used only for WPS ETCellImageData streams."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if self.data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise CompoundFileError(f"不是有效的 OLE/CFB 文件：{self.path}")
        if len(self.data) < 512:
            raise CompoundFileError(f"OLE/CFB 文件过短：{self.path}")

        self.major_version = self._u16(26)
        if self._u16(28) != 0xFFFE:
            raise CompoundFileError("不支持的 CFB 字节序")
        self.sector_size = 1 << self._u16(30)
        self.mini_sector_size = 1 << self._u16(32)
        self.number_of_fat_sectors = self._u32(44)
        self.first_directory_sector = self._u32(48)
        self.mini_stream_cutoff = self._u32(56)
        self.first_mini_fat_sector = self._u32(60)
        self.number_of_mini_fat_sectors = self._u32(64)
        first_difat_sector = self._u32(68)
        number_of_difat_sectors = self._u32(72)

        if self.sector_size not in {512, 4096} or self.mini_sector_size != 64:
            raise CompoundFileError("不支持的 CFB 扇区大小")

        difat = list(struct.unpack_from("<109I", self.data, 76))
        difat = [value for value in difat if value not in {FREE_SECTOR, END_OF_CHAIN}]
        current = first_difat_sector
        visited: set[int] = set()
        entries_per_difat = self.sector_size // 4 - 1
        for _ in range(number_of_difat_sectors):
            if current in visited or current in {FREE_SECTOR, END_OF_CHAIN}:
                break
            visited.add(current)
            sector = self._sector(current)
            values = struct.unpack_from(f"<{entries_per_difat + 1}I", sector)
            difat.extend(value for value in values[:-1] if value not in {FREE_SECTOR, END_OF_CHAIN})
            current = values[-1]
        fat_sector_ids = difat[: self.number_of_fat_sectors]
        if len(fat_sector_ids) != self.number_of_fat_sectors:
            raise CompoundFileError("CFB FAT 扇区数量不完整")

        self.fat: list[int] = []
        entries_per_sector = self.sector_size // 4
        for sector_id in fat_sector_ids:
            if sector_id in {FREE_SECTOR, END_OF_CHAIN, FAT_SECTOR, DIFAT_SECTOR}:
                raise CompoundFileError("CFB FAT 扇区编号非法")
            self.fat.extend(struct.unpack_from(f"<{entries_per_sector}I", self._sector(sector_id)))

        directory_data = self._read_regular_stream(self.first_directory_sector)
        self.entries = self._parse_directory(directory_data)
        root = next((entry for entry in self.entries if entry.object_type == 5), None)
        if root is None:
            raise CompoundFileError("CFB 缺少根目录项")
        self.root_entry = root
        self.mini_stream = self._read_regular_stream(root.start_sector, root.size)

        mini_fat_data = self._read_regular_stream(
            self.first_mini_fat_sector,
            self.number_of_mini_fat_sectors * self.sector_size,
        )
        self.mini_fat = list(struct.unpack(f"<{len(mini_fat_data) // 4}I", mini_fat_data)) if mini_fat_data else []

    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.data, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def _sector(self, sector_id: int) -> bytes:
        offset = (sector_id + 1) * self.sector_size
        end = offset + self.sector_size
        if sector_id < 0 or end > len(self.data):
            raise CompoundFileError(f"CFB 扇区越界：{sector_id}")
        return self.data[offset:end]

    @staticmethod
    def _chain(start_sector: int, table: list[int], label: str) -> list[int]:
        if start_sector in {FREE_SECTOR, END_OF_CHAIN}:
            return []
        result: list[int] = []
        visited: set[int] = set()
        current = start_sector
        while current not in {FREE_SECTOR, END_OF_CHAIN}:
            if current in visited:
                raise CompoundFileError(f"{label} 扇区链出现循环")
            if current < 0 or current >= len(table):
                raise CompoundFileError(f"{label} 扇区链越界：{current}")
            visited.add(current)
            result.append(current)
            current = table[current]
        return result

    def _read_regular_stream(self, start_sector: int, size: int | None = None) -> bytes:
        if start_sector in {FREE_SECTOR, END_OF_CHAIN}:
            return b""
        payload = b"".join(self._sector(sector_id) for sector_id in self._chain(start_sector, self.fat, "FAT"))
        return payload if size is None else payload[:size]

    def _read_mini_stream(self, start_sector: int, size: int) -> bytes:
        pieces: list[bytes] = []
        for mini_sector_id in self._chain(start_sector, self.mini_fat, "MiniFAT"):
            offset = mini_sector_id * self.mini_sector_size
            pieces.append(self.mini_stream[offset : offset + self.mini_sector_size])
        return b"".join(pieces)[:size]

    def _parse_directory(self, payload: bytes) -> list[DirectoryEntry]:
        result: list[DirectoryEntry] = []
        for offset in range(0, len(payload) - 127, 128):
            entry = payload[offset : offset + 128]
            name_length = struct.unpack_from("<H", entry, 64)[0]
            object_type = entry[66]
            if object_type == 0 or name_length < 2 or name_length > 64:
                continue
            name = entry[: name_length - 2].decode("utf-16le", errors="replace")
            start_sector = struct.unpack_from("<I", entry, 116)[0]
            size = struct.unpack_from("<Q", entry, 120)[0]
            if self.major_version == 3:
                size &= 0xFFFFFFFF
            result.append(DirectoryEntry(name, object_type, start_sector, size))
        return result

    def stream_names(self) -> list[str]:
        return [entry.name for entry in self.entries if entry.object_type == 2]

    def read_stream(self, name: str) -> bytes:
        wanted = name.casefold()
        entry = next(
            (item for item in self.entries if item.object_type == 2 and item.name.casefold() == wanted),
            None,
        )
        if entry is None:
            raise KeyError(name)
        if entry.size < self.mini_stream_cutoff:
            return self._read_mini_stream(entry.start_sector, entry.size)
        return self._read_regular_stream(entry.start_sector, entry.size)
