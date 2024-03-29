import logging
import time
import threading
import socket
from .midea.security import Security, MSGTYPE_HANDSHAKE_REQUEST, MSGTYPE_ENCRYPTED_REQUEST
from .midea.message_parser import MessageParser
from .midea.command import CommandRequest, CommandSet
from .midea.packet_builder import PacketBuilder

_LOGGER = logging.getLogger(__name__)


class DeviceState:
    def __init__(self):
        self.prompt_tone = True
        self.power = False
        self.mode = 0
        self.fan_speed = 102
        self.swing_vertical = False
        self.swing_horizontal = False
        self.target_temperature = 20.0
        self.indoor_temperature = 20.0
        self.outdoor_temperature = 20.0
        self.keep_warm = False
        self.eco_mode = False
        self.indirect_wind = False


class DeviceManager(threading.Thread):
    def __init__(self, device_id, host, port, token, key, protocol, model):
        threading.Thread.__init__(self)
        self._lock = threading.Lock()
        self._socket = None
        self._host = host
        self._port = port
        self._is_run = False
        self._on_updates = []
        self._timeout_counter = 0
        self._security = Security()
        self._token = token
        self._key = key
        self._buffer = b''
        self._device_id = device_id
        self._protocol = protocol
        self._model = model
        self._status = DeviceState()
        self._updates = []


    def run(self):
        counter = 0
        while self._is_run:
            with self._lock:
                while self._socket is None:
                    _LOGGER.debug(f"Try to connect to device {self._device_id}")
                    if self.open(False) is False:
                        time.sleep(10)
                    if not self._is_run:
                        if self._socket is not None:
                            self._socket.close()
                            self._socket = None
                        return
            self._timeout_counter = 0
            while self._is_run:
                try:
                    msg = self._socket.recv(512)
                    if not self._is_run:
                        break
                    msg_len = len(msg)
                    if msg_len == 0:
                        raise socket.error
                    self._timeout_counter = 0
                    #Message process
                    self.process_message(msg)

                except socket.timeout:
                    self._timeout_counter = self._timeout_counter + 1
                    if self._timeout_counter >= 10:
                        _LOGGER.debug(f"Heartbeat timeout detected, reconnecting")
                        with self._lock:
                            self._socket.close()
                            self._socket = None
                            break
                    #Send Heartbeat
                    self.send_heartbeat()
                    if counter >= 5:
                        self.refresh_status()
                        counter = 0
                    else:
                        counter = counter + 1
                except socket.error:
                    _LOGGER.debug(f"Except socket.error {socket.error} raised in socket.recv()")
                    with self._lock:
                        self._socket.close()
                        self._socket = None
                        break
                except Exception as e:
                    _LOGGER.debug(f"Except {e} raised")
                    with self._lock:
                        self._socket.close()
                        self._socket = None
                        break

    def send_message(self, data):
        if self._protocol == 3:
            self.send_message_V3(data, msg_type=MSGTYPE_ENCRYPTED_REQUEST)
        else:
            self.send_message_V2(data)

    def send_message_V2(self, data):
        if self._socket is not None:
            with self._lock:
                self._socket.send(data)

    def send_message_V3(self, data, msg_type=MSGTYPE_ENCRYPTED_REQUEST):
        data = self._security.encode_8370(data, msg_type)
        self.send_message_V2(data)

    def process_message(self, msg):
        if self._protocol == 3:
            messages, self._buffer = self._security.decode_8370(self._buffer + msg)
        else:
            messages = [msg]
        for message in messages:
            # It's not heartbeat
            if len(message) > 40 + 16 and message[3] != 0x10:
                message = self._security.aes_decrypt(message[40:-16])
                parser = MessageParser(message)
                self.parse_message(parser)

    def parse_message(self, parser: MessageParser):
        updates = {}
        if parser.msg_type == 0x2C0 or parser.msg_type == 0x3C0:
            self._status.power = parser.power
            self._status.mode = parser.mode
            self._status.fan_speed = parser.fan_speed
            self._status.swing_vertical = parser.swing_vertical
            self._status.swing_horizontal = parser.swing_horizontal
            self._status.target_temperature = parser.target_temperature
            self._status.indoor_temperature = parser.indoor_temperature
            if parser.outdoor_temperature != 102.0:
                self._status.outdoor_temperature = parser.outdoor_temperature
            else:
                self._status.outdoor_temperature = 0.0
            self._status.keep_warm = parser.keep_warm
            self._status.eco_mode = parser.eco_mode
            if not self._status.power:
                self._status.indirect_wind = False
            elif self._status.swing_vertical:
                self._status.indirect_wind = False
            updates = {
                "power": self._status.power,
                "mode": self._status.mode,
                "fan_speed": self._status.fan_speed,
                "swing_vertical": self._status.swing_vertical,
                "swing_horizontal": self._status.swing_horizontal,
                "target_temperature": self._status.target_temperature,
                "indoor_temperature": self._status.indoor_temperature,
                "outdoor_temperature": self._status.outdoor_temperature,
                "keep_warm": self._status.keep_warm,
                "eco_mode": self._status.eco_mode,
                "indirect_wind": self._status.indirect_wind
            }
        elif parser.msg_type == 0x4A1:
            self._status.indoor_temperature = parser.indoor_temperature
            if parser.outdoor_temperature != 102.0:
                self._status.outdoor_temperature = parser.outdoor_temperature
            else:
                self._status.outdoor_temperature = 0.0
            updates = {
                "indoor_temperature":  self._status.indoor_temperature,
                "outdoor_temperature":  self._status.outdoor_temperature
            }
            pass
        elif parser.msg_type == 0x5A0:
            self._status.power = parser.power
            self._status.mode = parser.mode
            self._status.fan_speed = parser.fan_speed
            self._status.swing_vertical = parser.swing_vertical
            self._status.swing_horizontal = parser.swing_horizontal
            self._status.target_temperature = parser.target_temperature
            self._status.keep_warm = parser.keep_warm
            self._status.eco_mode = parser.eco_mode
            if not self._status.power:
                self._status.indirect_wind = False
            elif self._status.swing_vertical:
                self._status.indirect_wind = False
            updates = {
                "power":  self._status.power,
                "mode":  self._status.mode,
                "fan_speed":  self._status.fan_speed,
                "swing_vertical":  self._status.swing_vertical,
                "swing_horizontal":  self._status.swing_horizontal,
                "target_temperature":  self._status.target_temperature,
                "keep_warm":  self._status.keep_warm,
                "eco_mode":  self._status.eco_mode,
                "indirect_wind": self._status.indirect_wind
            }
        elif parser.msg_type == 0x5B5:
            self._status.indirect_wind = parser.indirect_wind
            updates = {"indirect_wind":  self._status.indirect_wind}
            pass
        else:
            _LOGGER.debug(f"Unknown message {parser}")
        for update in self._updates:
            update(updates)
        _LOGGER.debug(f"Received message: {parser}")
        _LOGGER.debug(updates)

    def open(self, start_thread):
        result = False
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(10)
            self._socket.connect((self._host, self._port))
            #auth
            if self._protocol == 3:
                self.authenticate()
            self.refresh_status(wait_response=True)
            result = True
        except socket.timeout:
            _LOGGER.debug(f"Socket connect timeout")
            self._socket.close()
            self._socket = None
        except socket.error:
            _LOGGER.debug(f"Socket connect error {socket.error}")
            self._socket.close()
            self._socket = None
        except Exception as e:
            _LOGGER.error(f"Socket connect error {e}")
        if start_thread:
            self._is_run = True
            threading.Thread.start(self)
        return result

    def close(self):
        self._is_run = False
        self._socket.close()
        self._socket = None

    def authenticate(self):
        request = self._security.encode_8370(
            self._token, MSGTYPE_HANDSHAKE_REQUEST)
        with self._lock:
            self._socket.send(request)
        response = self._socket.recv(512)
        response = response[8: 72]
        self._security.tcp_key(response, self._key)

    def refresh_status(self, wait_response=False):
        cmd = CommandRequest().finalize()
        msg = PacketBuilder(self._device_id, cmd).finalize()
        self.send_message(msg)
        if wait_response:
            msg = self._socket.recv(512)
            msg_len = len(msg)
            if msg_len == 0:
                raise socket.error
            self.process_message(msg)

    @property
    def model(self):
        return self._model

    @property
    def device_id(self):
        return self._device_id

    def get_status(self, attr):
        if hasattr(self._status, attr):
            return self._status.__getattribute__(attr)
        else:
            return None

    def send_heartbeat(self):
        msg = PacketBuilder(self._device_id, bytearray([0x00])).finalize(msg_type=0)
        self.send_message(msg)

    def make_command_set(self):
        cmd = CommandSet()
        cmd.set_prompt_tone(self._status.prompt_tone)
        cmd.set_power(self._status.power)
        cmd.set_mode(self._status.mode)
        cmd.set_fan_speed(self._status.fan_speed)
        cmd.set_swing(self._status.swing_vertical, self._status.swing_horizontal)
        cmd.set_target_temperature(self._status.target_temperature)
        cmd.set_keep_warm(self._status.keep_warm)
        cmd.set_eco_mode(self._status.eco_mode)
        cmd.set_indirect_wind(self._status.indirect_wind)
        return cmd

    def set_status(self, cmd: CommandSet):
        data = cmd.finalize()
        msg = PacketBuilder(self._device_id, data).finalize()
        self.send_message(msg)

    def set_prompt_tone(self, prompt_tone: bool):
        _LOGGER.debug(f"Set device {self._device_id} prompt_tone={prompt_tone}")
        self._status.prompt_tone = prompt_tone

    def set_power(self, power: bool):
        _LOGGER.debug(f"Set device {self._device_id} power={power}")
        cmd = self.make_command_set()
        cmd.set_power(power)
        self.set_status(cmd)

    def set_mode(self, mode: int):
        _LOGGER.debug(f"Set device {self._device_id} mode={mode}")
        cmd = self.make_command_set()
        cmd.set_mode(mode)
        cmd.set_power(True)
        self.set_status(cmd)

    def set_target_temperature(self, temperature: float):
        _LOGGER.debug(f"Set device {self._device_id} temperature={temperature}")
        cmd = self.make_command_set()
        cmd.set_target_temperature(temperature)
        self.set_status(cmd)

    def set_swing_vertical(self, swing_vertical):
        _LOGGER.debug(f"Set device {self._device_id} swing_vertical={swing_vertical}")
        cmd = self.make_command_set()
        cmd.set_swing(swing_vertical, self._status.swing_horizontal)
        self.set_status(cmd)

    def set_swing_horizontal(self, swing_horizontal):
        _LOGGER.debug(f"Set device {self._device_id} swing_horizontal={swing_horizontal}")
        cmd = self.make_command_set()
        cmd.set_swing(self._status.swing_vertical, swing_horizontal)
        self.set_status(cmd)

    def set_swing(self, swing_vertical, swing_horizontal):
        _LOGGER.debug(f"Set device {self._device_id} swing_vertical={swing_vertical}, "
                      f"swing_horizontal={swing_horizontal}")
        cmd = self.make_command_set()
        cmd.set_swing(swing_vertical, swing_horizontal)
        self.set_status(cmd)

    def set_fan_speed(self, fan_speed):
        _LOGGER.debug(f"Set device {self._device_id} fan_speed={fan_speed}")
        cmd = self.make_command_set()
        cmd.set_fan_speed(fan_speed)
        self.set_status(cmd)

    def set_keep_warm(self, keep_warm):
        _LOGGER.debug(f"Set device {self._device_id} keep_warm={keep_warm}")
        cmd = self.make_command_set()
        cmd.set_keep_warm(keep_warm)
        self.set_status(cmd)

    def set_eco_mode(self, eco_mode):
        _LOGGER.debug(f"Set device {self._device_id} eco_mode={eco_mode}")
        cmd = self.make_command_set()
        cmd.set_eco_mode(eco_mode)
        self.set_status(cmd)

    def set_indirect_wind(self, indirect_wind):
        _LOGGER.debug(f"Set device {self._device_id} indirect_wind={indirect_wind}")
        cmd = self.make_command_set()
        cmd.set_indirect_wind(indirect_wind)
        self.set_status(cmd)

    def add_update(self, update):
        self._updates.append(update)

