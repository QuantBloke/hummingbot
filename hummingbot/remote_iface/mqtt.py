#!/usr/bin/env python

import asyncio
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from logging import Handler, LogRecord
from typing import TYPE_CHECKING, List, Tuple

# from hummingbot.strategy.strategy_py_base import StrategyPyBase
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.client.hummingbot_application import HummingbotApplication
    from hummingbot.core.event.event_listener import EventListener

from commlib.node import Node
from commlib.transports.mqtt import ConnectionParameters as MQTTConnectionParameters

from hummingbot.core.event import events
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.pubsub import PubSub
from hummingbot.notifier.notifier_base import NotifierBase
from hummingbot.remote_iface.messages import (
    MQTT_STATUS_CODE,
    BalanceLimitCommandMessage,
    BalancePaperCommandMessage,
    ConfigCommandMessage,
    EventMessage,
    HistoryCommandMessage,
    ImportCommandMessage,
    LogMessage,
    NotifyMessage,
    StartCommandMessage,
    StatusCommandMessage,
    StopCommandMessage,
)

mqtts_logger: HummingbotLogger = None


def get_timestamp(days_ago: float = 0.) -> float:
    return time.time() - (60. * 60. * 24. * days_ago)


class MQTTCommands:
    START_URI = 'hbot/$UID/start'
    STOP_URI = 'hbot/$UID/stop'
    CONFIG_URI = 'hbot/$UID/config'
    IMPORT_URI = 'hbot/$UID/import'
    STATUS_URI = 'hbot/$UID/status'
    HISTORY_URI = 'hbot/$UID/history'
    BALANCE_LIMIT_URI = 'hbot/$UID/balance/limit'
    BALANCE_PAPER_URI = 'hbot/$UID/balance/paper'

    def __init__(self,
                 hb_app: "HummingbotApplication",
                 mqtt_node: Node):
        self._hb_app = hb_app
        self.node = mqtt_node
        self.logger = self._hb_app.logger
        self._ev_loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()

        self.START_URI = self.START_URI.replace('$UID', hb_app.uid)
        self.STOP_URI = self.STOP_URI.replace('$UID', hb_app.uid)
        self.CONFIG_URI = self.CONFIG_URI.replace('$UID', hb_app.uid)
        self.IMPORT_URI = self.IMPORT_URI.replace('$UID', hb_app.uid)
        self.STATUS_URI = self.STATUS_URI.replace('$UID', hb_app.uid)
        self.HISTORY_URI = self.HISTORY_URI.replace('$UID', hb_app.uid)
        self.BALANCE_LIMIT_URI = self.BALANCE_LIMIT_URI.replace('$UID', hb_app.uid)
        self.BALANCE_PAPER_URI = self.BALANCE_PAPER_URI.replace('$UID', hb_app.uid)

        self._init_commands()

    def _init_commands(self):
        self.node.create_rpc(
            rpc_name=self.START_URI,
            msg_type=StartCommandMessage,
            on_request=self._on_cmd_start
        )
        self.node.create_rpc(
            rpc_name=self.STOP_URI,
            msg_type=StopCommandMessage,
            on_request=self._on_cmd_stop
        )
        self.node.create_rpc(
            rpc_name=self.CONFIG_URI,
            msg_type=ConfigCommandMessage,
            on_request=self._on_cmd_config
        )
        self.node.create_rpc(
            rpc_name=self.IMPORT_URI,
            msg_type=ImportCommandMessage,
            on_request=self._on_cmd_import
        )
        self.node.create_rpc(
            rpc_name=self.STATUS_URI,
            msg_type=StatusCommandMessage,
            on_request=self._on_cmd_status
        )
        self.node.create_rpc(
            rpc_name=self.HISTORY_URI,
            msg_type=HistoryCommandMessage,
            on_request=self._on_cmd_history
        )
        self.node.create_rpc(
            rpc_name=self.BALANCE_LIMIT_URI,
            msg_type=BalanceLimitCommandMessage,
            on_request=self._on_cmd_balance_limit
        )
        self.node.create_rpc(
            rpc_name=self.BALANCE_PAPER_URI,
            msg_type=BalancePaperCommandMessage,
            on_request=self._on_cmd_balance_paper
        )

    def _on_cmd_start(self, msg: StartCommandMessage.Request):
        response = StartCommandMessage.Response()
        try:
            self._hb_app.start(
                log_level=msg.log_level,
                restore=msg.restore,
                script=msg.script,
                is_quickstart=msg.is_quickstart
            )
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_stop(self, msg: StopCommandMessage.Request):
        response = StopCommandMessage.Response()
        try:
            self._hb_app.stop(
                skip_order_cancellation=msg.skip_order_cancellation
            )
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_config(self, msg: ConfigCommandMessage.Request):
        response = ConfigCommandMessage.Response()
        try:
            if len(msg.params) == 0:
                self._hb_app.config()
            else:
                for param in msg.params:
                    if param[0] in self._hb_app.configurable_keys():
                        self._hb_app.config(param[0], param[1])
                        response.changes.append((param[0], param[1]))
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_import(self, msg: ImportCommandMessage.Request):
        response = ImportCommandMessage.Response()
        strategy_name = msg.strategy
        if strategy_name is not None:
            strategy_file_name = f'{strategy_name}.yml'
            try:
                self._hb_app.import_command(strategy_file_name)
            except Exception as e:
                self._hb_app.notify(str(e))
                response.status = MQTT_STATUS_CODE.ERROR
                response.msg = str(e)
        return response

    def _on_cmd_status(self, msg: StatusCommandMessage.Request):
        response = StatusCommandMessage.Response()
        try:
            _status = self._ev_loop.run_until_complete(self._hb_app.strategy_status()).strip()
            response.data = _status
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_history(self, msg: HistoryCommandMessage.Request):
        response = HistoryCommandMessage.Response()
        try:
            self._hb_app.history(msg.days, msg.verbose, msg.precision)
            trades = self._hb_app.get_history_trades(msg.days)
            if trades:
                response.trades = trades.all()
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_balance_limit(self, msg: BalanceLimitCommandMessage.Request):
        response = BalanceLimitCommandMessage.Response()
        try:
            data = self._hb_app.balance(
                'limit',
                [msg.exchange, msg.asset, msg.amount]
            )
            response.data = data
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_cmd_balance_paper(self, msg: BalancePaperCommandMessage.Request):
        response = BalancePaperCommandMessage.Response()
        try:
            data = self._hb_app.balance(
                'paper',
                [msg.asset, msg.amount]
            )
            response.data = data
        except Exception as e:
            response.status = MQTT_STATUS_CODE.ERROR
            response.msg = str(e)
        return response

    def _on_get_market_data(self, msg):
        _response = {
            'status': 200,
            'msg': '',
            'market_data': {}
        }
        try:
            market_data = self._hb_app.strategy.market_status_df()
            _response['market_data'] = market_data
        except Exception as e:
            _response['msg'] = str(e)
            _response['status'] = 400
        return _response


class MQTTEventForwarder:
    EVENT_URI = 'hbot/$UID/events'

    @classmethod
    def logger(cls) -> HummingbotLogger:
        global mqtts_logger
        if mqtts_logger is None:
            mqtts_logger = HummingbotLogger("MQTTGateway")
        return mqtts_logger

    def __init__(self,
                 hb_app: "HummingbotApplication",
                 mqtt_node: Node):

        if threading.current_thread() != threading.main_thread():
            raise EnvironmentError(
                "MQTTEventForwarder can only be initialized from the main thread."
            )

        self._ev_loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self._hb_app = hb_app
        self._node = mqtt_node
        self._markets: List[ConnectorBase] = list(self._hb_app.markets.values())

        self._topic = self.EVENT_URI.replace('$UID', self._hb_app.uid)

        self._mqtt_fowarder: SourceInfoEventForwarder = \
            SourceInfoEventForwarder(self._send_mqtt_event)
        self._market_event_pairs: List[Tuple[int, EventListener]] = [
            (events.MarketEvent.BuyOrderCreated, self._mqtt_fowarder),
            (events.MarketEvent.BuyOrderCompleted, self._mqtt_fowarder),
            (events.MarketEvent.SellOrderCreated, self._mqtt_fowarder),
            (events.MarketEvent.SellOrderCompleted, self._mqtt_fowarder),
            (events.MarketEvent.OrderFilled, self._mqtt_fowarder),
            (events.MarketEvent.OrderFailure, self._mqtt_fowarder),
            (events.MarketEvent.OrderCancelled, self._mqtt_fowarder),
            (events.MarketEvent.OrderExpired, self._mqtt_fowarder),
            (events.MarketEvent.FundingPaymentCompleted, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionLiquidityAdded, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionLiquidityRemoved, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionUpdate, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionUpdateFailure, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionFeeCollected, self._mqtt_fowarder),
            (events.MarketEvent.RangePositionClosed, self._mqtt_fowarder),
        ]
        self._app_event_pairs: List[Tuple[int, EventListener]] = []

        self.event_fw_pub = self._node.create_publisher(
            topic=self._topic, msg_type=EventMessage
        )
        self.start_event_listener()

    def _send_mqtt_event(self, event_tag: int, pubsub: PubSub, event):
        if threading.current_thread() != threading.main_thread():
            self._ev_loop.call_soon_threadsafe(self._send_mqtt_event, event_tag, pubsub, event)
            return

        try:
            event_types = {
                events.MarketEvent.BuyOrderCreated.value: "BuyOrderCreated",
                events.MarketEvent.BuyOrderCompleted.value: "BuyOrderCompleted",
                events.MarketEvent.SellOrderCreated.value: "SellOrderCreated",
                events.MarketEvent.SellOrderCompleted.value: "SellOrderCompleted",
                events.MarketEvent.OrderFilled.value: "OrderFilled",
                events.MarketEvent.OrderCancelled.value: "OrderCancelled",
                events.MarketEvent.OrderExpired.value: "OrderExpired",
                events.MarketEvent.OrderFailure.value: "OrderFailure",
                events.MarketEvent.FundingPaymentCompleted.value: "FundingPaymentCompleted",
                events.MarketEvent.RangePositionLiquidityAdded.value: "RangePositionLiquidityAdded",
                events.MarketEvent.RangePositionLiquidityRemoved.value: "RangePositionLiquidityRemoved",
                events.MarketEvent.RangePositionUpdate.value: "RangePositionUpdate",
                events.MarketEvent.RangePositionUpdateFailure.value: "RangePositionUpdateFailure",
                events.MarketEvent.RangePositionFeeCollected.value: "RangePositionFeeCollected",
                events.MarketEvent.RangePositionClosed.value: "RangePositionClosed",
            }
            event_type = event_types[event_tag]
        except KeyError:
            event_type = "Unknown"

        if is_dataclass(event):
            event_data = asdict(event)
        elif isinstance(event, tuple) and hasattr(event, '_fields'):
            event_data = event._asdict()
        else:
            try:
                event_data = dict(event)
            except (TypeError, ValueError):
                event_data = {}

        try:
            timestamp = event_data.pop('timestamp')
        except KeyError:
            timestamp = datetime.now().timestamp()

        self.event_fw_pub.publish(
            EventMessage(
                timestamp=int(timestamp),
                type=event_type,
                data=event_data
            )
        )

    def start_event_listener(self):
        for market in self._markets:
            for event_pair in self._market_event_pairs:
                market.add_listener(event_pair[0], event_pair[1])
                self.logger().info(
                    f'Created MQTT bridge for event: {event_pair[0]}, {event_pair[1]}'
                )
        for event_pair in self._app_event_pairs:
            self._hb_app.app.add_listener(event_pair[0], event_pair[1])

    def stop_event_listener(self):
        for market in self._markets:
            for event_pair in self._market_event_pairs:
                market.remove_listener(event_pair[0], event_pair[1])
        for event_pair in self._app_event_pairs:
            self._hb_app.app.remove_listener(event_pair[0], event_pair[1])


class MQTTNotifier(NotifierBase):
    NOTIFY_URI = 'hbot/$UID/notify'

    def __init__(self,
                 hb_app: "HummingbotApplication",
                 mqtt_node: Node,
                 topic: str = '') -> None:
        super().__init__()
        if topic in (None, ''):
            topic = self.NOTIFY_URI.replace('$UID', hb_app.uid)
        self._topic = topic
        self._node = mqtt_node
        self._hb_app = hb_app
        self.notify_pub = self._node.create_publisher(topic=self._topic,
                                                      msg_type=NotifyMessage)

    def add_msg_to_queue(self, msg: str):
        self.notify_pub.publish(NotifyMessage(msg=msg))

    def start(self):
        pass

    def stop(self):
        pass


class MQTTGateway(Node):
    NODE_NAME = 'hbot.$UID'
    HEARTBEAT_URI = 'hbot/$UID/hb'

    @classmethod
    def logger(cls) -> HummingbotLogger:
        global mqtts_logger
        if mqtts_logger is None:
            mqtts_logger = HummingbotLogger("MQTTGateway")
        return mqtts_logger

    def __init__(self,
                 hb_app: "HummingbotApplication",
                 *args, **kwargs):
        self._hb_app = hb_app
        self.HEARTBEAT_URI = self.HEARTBEAT_URI.replace('$UID', hb_app.uid)

        self._params = self._create_mqtt_params_from_conf()

        super().__init__(
            node_name=self.NODE_NAME.replace('$UID', hb_app.uid),
            connection_params=self._params,
            heartbeat_uri=self.HEARTBEAT_URI,
            debug=True,
            *args,
            **kwargs
        )

    def patch_logger_class(self):
        HummingbotLogger._mqtt_handler = MQTTLogHandler(self._hb_app, self)

    def start_notifier(self):
        if self._hb_app.client_config_map.mqtt_bridge.mqtt_notifier:
            self.logger().info('Starting MQTT Notifier')
            self._notifier = MQTTNotifier(self._hb_app, self)
            self._hb_app.notifiers.append(self._notifier)

    def start_commands(self):
        if self._hb_app.client_config_map.mqtt_bridge.mqtt_commands:
            self.logger().info('Starting MQTT Remote Commands')
            self._commands = MQTTCommands(self._hb_app, self)

    def start_event_fw(self):
        if self._hb_app.client_config_map.mqtt_bridge.mqtt_events:
            self.logger().info('Starting MQTT Remote Events')
            self.mqtt_event_forwarder = MQTTEventForwarder(self._hb_app, self)

    def _create_mqtt_params_from_conf(self):
        host = self._hb_app.client_config_map.mqtt_bridge.mqtt_host
        port = self._hb_app.client_config_map.mqtt_bridge.mqtt_port
        username = self._hb_app.client_config_map.mqtt_bridge.mqtt_username
        password = self._hb_app.client_config_map.mqtt_bridge.mqtt_password
        ssl = self._hb_app.client_config_map.mqtt_bridge.mqtt_ssl
        conn_params = MQTTConnectionParameters(
            host=host,
            port=int(port),
            username=username,
            password=password,
            ssl=ssl
        )
        return conn_params


class MQTTLogHandler(Handler):
    MQTT_URI = 'hbot/$UID/log'

    def __init__(self,
                 hb_app: "HummingbotApplication",
                 mqtt_node: Node,
                 mqtt_topic: str = ''):
        self._hb_app = hb_app
        self._node = mqtt_node
        if mqtt_topic in ('', None):
            mqtt_topic = self.MQTT_URI.replace('$UID', self._hb_app.uid)
        self._topic = mqtt_topic
        super().__init__()
        self.log_pub = self._node.create_publisher(topic=self._topic,
                                                   msg_type=LogMessage)

    def emit(self, record: LogRecord):
        msg_str = self.format(record)
        msg = LogMessage(
            timestamp=time.time(),
            msg=msg_str,
            level_no=record.levelno,
            level_name=record.levelname,
            logger_name=record.name

        )
        self.log_pub.publish(msg)
        return msg
