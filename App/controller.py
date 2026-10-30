import display
import server


def toggle_light():
    config = server.config
    state = config["relayLight"]["state"]

    print("Light state:", state)
    print("Controller Light toggled")

    server.light_relay.toggle()
    display.refresh()


def toggle_fan():
    config = server.config
    state = config["relayFan"]["state"]

    print("Fan state:", state)
    print("Controller Fan toggled")

    server.fan_relay.toggle()
    display.refresh()


def toggle_auto():
    config = server.config

    config["auto"]["enabled"] = not config["auto"]["enabled"]

    server.config_store.save(config)

    if config["auto"]["enabled"]:
        server.apply_auto_logic()

    display.refresh()
