import logging
import sys
import asyncio
from threading import Thread, Event

import aioxmpp

from spade.message import Message

logger = logging.getLogger('spade.Agent')


class Agent(object):
    def __init__(self, jid, password, verify_security=False):
        self.jid = aioxmpp.JID.fromstr(jid)
        self.password = password

        self.behaviours = []
        self._values = {}

        self.aiothread = AioThread(self.jid, password, verify_security)

        self.aiothread.start()
        self.aiothread.event.wait()

        # obtain an instance of the service
        message_dispatcher = self.client.summon(
            aioxmpp.dispatcher.SimpleMessageDispatcher
        )

        # register a message callback here
        message_dispatcher.register_callback(
            aioxmpp.MessageType.CHAT,
            None,
            self.message_received,
        )

    @property
    def client(self):
        return self.aiothread.client

    @property
    def stream(self):
        return self.aiothread.stream

    def submit(self, coro):
        return self.aiothread.submit(coro)

    def add_behaviour(self, behaviour, template=None):
        behaviour.set_aiothread(self.aiothread)
        behaviour.set_agent(self)
        behaviour.set_template = template
        self.behaviours.append(behaviour)
        behaviour.start()

    def remove_behaviour(self, behaviour):
        if behaviour not in self.behaviours:
            raise ValueError("This behaviour is not registered")
        index = self.behaviours.index(behaviour)
        self.behaviours[index].kill()
        self.behaviours.pop(index)

    def stop(self):
        for behav in self.behaviours:
            behav.kill()
        self.aiothread.finalize()

    def set(self, name, value):
        self._values[name] = value

    def get(self, name):
        return self._values[name]

    def send(self, msg):
        if not msg.sender:
            msg.sender = self.jid
            logger.debug(f"Adding agent's jid as sender to message: {msg}")
        return self.submit(self.stream.send(msg.prepare()))

    def message_received(self, msg):
        logger.debug(f"Got message: {msg}")

        msg = Message.from_node(msg)
        for behaviour in (x for x in self.behaviours if x.match(msg)):
            self.submit(behaviour.enqueue(msg))
            logger.debug(f"Message enqueued to behaviour: {behaviour}")


class AioThread(Thread):
    def __init__(self, jid, password, verify_security, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = Event()
        self.conn_coro = None
        self.stream = None

        self.loop = asyncio.new_event_loop()
        self.loop.set_debug(True)
        asyncio.set_event_loop(self.loop)
        self.client = aioxmpp.PresenceManagedClient(jid,
                                                    aioxmpp.make_security_layer(password,
                                                                                no_verify=not verify_security),
                                                    loop=self.loop)
        self.connect()

    def run(self):
        self.loop.call_soon(self.event.set)
        self.loop.run_forever()

    def connect(self):
        self.conn_coro = self.client.connected()
        aenter = type(self.conn_coro).__aenter__(self.conn_coro)
        self.stream = self.loop.run_until_complete(aenter)

    def submit(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, loop=self.loop)
        return fut

    def finalize(self):
        aexit = self.conn_coro.__aexit__(*sys.exc_info())
        asyncio.run_coroutine_threadsafe(aexit, loop=self.loop)
        # asyncio.gather(*asyncio.Task.all_tasks()).cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.join()