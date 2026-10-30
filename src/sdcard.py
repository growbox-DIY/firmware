from time import sleep_ms, ticks_ms, ticks_diff

from micropython import const

_CMD_TIMEOUT = const(100)
_TOKEN_DATA = const(0xFE)


class SDCard:
    def __init__(
            self,
            spi,
            cs,
            baudrate=400000
    ):
        self.spi = spi
        self.cs = cs
        self.baudrate = baudrate

        self.cs.init(
            cs.OUT,
            value=1
        )

        self.cmdbuf = bytearray(6)
        self.dummy = bytearray([0xFF])

        # card init must run at <= 400 kHz
        self.spi.init(
            baudrate=400000,
            polarity=0,
            phase=0
        )

        self._init_card()

    def _clock(self, count=1):
        for _ in range(count):
            self.spi.write(
                self.dummy
            )

    def _read_response(
            self,
            timeout=_CMD_TIMEOUT
    ):
        for _ in range(timeout):
            response = self.spi.read(
                1,
                0xFF
            )[0]

            if not (response & 0x80):
                return response

        return 0xFF

    def _cmd(
            self,
            cmd,
            arg=0,
            crc=0x01
    ):
        self.cmdbuf[0] = 0x40 | cmd
        self.cmdbuf[1] = (
                                 arg >> 24
                         ) & 0xFF
        self.cmdbuf[2] = (
                                 arg >> 16
                         ) & 0xFF
        self.cmdbuf[3] = (
                                 arg >> 8
                         ) & 0xFF
        self.cmdbuf[4] = arg & 0xFF
        self.cmdbuf[5] = crc

        self.cs.value(0)

        self.spi.write(
            self.cmdbuf
        )

        return self._read_response()

    def _end_command(self):
        self.cs.value(1)

        self.spi.write(
            self.dummy
        )

    def _init_card(self):
        self.cs.value(1)

        self._clock(10)

        # after power-up the card may not be ready yet - several CMD0 attempts
        for _ in range(5):
            response = self._cmd(
                0,
                0,
                0x95
            )

            self._end_command()

            if response == 1:
                break

            sleep_ms(50)
            self._clock(10)

        if response != 1:
            raise OSError(
                "no SD card: CMD0"
            )

        response = self._cmd(
            8,
            0x1AA,
            0x87
        )

        if response == 1:
            self.spi.read(
                4,
                0xFF
            )

            self._end_command()

            for _ in range(1000):
                response = self._cmd(
                    55,
                    0
                )

                self._end_command()

                if response > 1:
                    continue

                response = self._cmd(
                    41,
                    0x40000000
                )

                self._end_command()

                if response == 0:
                    break

                sleep_ms(1)

            else:
                raise OSError(
                    "SD init timeout: ACMD41"
                )

            response = self._cmd(
                58,
                0
            )

            if response == 0:
                self.spi.read(
                    4,
                    0xFF
                )

            self._end_command()

        else:
            self._end_command()

            for _ in range(1000):
                response = self._cmd(
                    55,
                    0
                )

                self._end_command()

                response = self._cmd(
                    41,
                    0
                )

                self._end_command()

                if response == 0:
                    break

                sleep_ms(1)

            else:
                raise OSError(
                    "SD init timeout: legacy ACMD41"
                )

        response = self._cmd(
            16,
            512
        )

        self._end_command()

        if response != 0:
            raise OSError(
                "SD block size error"
            )

        # after init we can speed up
        self.spi.init(
            baudrate=self.baudrate,
            polarity=0,
            phase=0
        )

    def _wait_for_token(self, token):
        for _ in range(
                _CMD_TIMEOUT * 10
        ):
            if (
                    self.spi.read(
                        1,
                        0xFF
                    )[0] == token
            ):
                return True

        return False

    def _wait_until_ready(self, timeout_ms=500):
        # time limit, not an iteration count - with faster SPI
        # 1000 reads pass faster than the card finishes writing
        start = ticks_ms()

        while ticks_diff(ticks_ms(), start) < timeout_ms:
            if self.spi.read(
                    1,
                    0xFF
            )[0] == 0xFF:
                return True

        raise OSError(
            "SD busy timeout"
        )

    def readblocks(
            self,
            block_num,
            buf
    ):
        nblocks = len(buf) // 512

        if nblocks == 1:
            response = self._cmd(
                17,
                block_num
            )

            if response != 0:
                self._end_command()

                raise OSError(
                    "SD read error"
                )

            if not self._wait_for_token(
                    _TOKEN_DATA
            ):
                self._end_command()

                raise OSError(
                    "SD read timeout"
                )

            self.spi.readinto(buf)

            self.spi.read(
                2,
                0xFF
            )

            self._end_command()

            return

        response = self._cmd(
            18,
            block_num
        )

        if response != 0:
            self._end_command()

            raise OSError(
                "SD multi-read error"
            )

        for offset in range(
                0,
                len(buf),
                512
        ):
            if not self._wait_for_token(
                    _TOKEN_DATA
            ):
                self._end_command()

                raise OSError(
                    "SD multi-read timeout"
                )

            self.spi.readinto(
                memoryview(buf)[
                    offset:offset + 512
                ]
            )

            self.spi.read(
                2,
                0xFF
            )

        self._cmd(
            12,
            0
        )

        # after CMD12 the card is busy - wait before releasing CS
        self._wait_until_ready()

        self._end_command()

    def writeblocks(
            self,
            block_num,
            buf
    ):
        nblocks = len(buf) // 512

        if nblocks == 1:
            response = self._cmd(
                24,
                block_num
            )

            if response != 0:
                self._end_command()

                raise OSError(
                    "SD write error"
                )

            self.spi.write(
                bytes([_TOKEN_DATA])
            )

            self.spi.write(buf)

            self.spi.write(
                b"\xFF\xFF"
            )

            response = self.spi.read(
                1,
                0xFF
            )[0]

            if (
                    response & 0x1F
            ) != 0x05:
                self._end_command()

                raise OSError(
                    "SD write rejected"
                )

            self._wait_until_ready()

            self._end_command()

            return

        response = self._cmd(
            25,
            block_num
        )

        if response != 0:
            self._end_command()

            raise OSError(
                "SD multi-write error"
            )

        for offset in range(
                0,
                len(buf),
                512
        ):
            self.spi.write(
                bytes([_TOKEN_DATA])
            )

            self.spi.write(
                memoryview(buf)[
                    offset:offset + 512
                ]
            )

            self.spi.write(
                b"\xFF\xFF"
            )

            response = self.spi.read(
                1,
                0xFF
            )[0]

            if (
                    response & 0x1F
            ) != 0x05:
                self._end_command()

                raise OSError(
                    "SD multi-write rejected"
                )

            self._wait_until_ready()

        self.spi.write(
            bytes([0xFD])
        )

        # stop token -> card is writing, need to wait
        self.spi.read(1, 0xFF)
        self._wait_until_ready()

        self._end_command()

    def ioctl(
            self,
            op,
            arg
    ):
        if op == 4:
            return 7744512

        if op == 5:
            return 512

        if op == 6:
            return 1

        return 0
