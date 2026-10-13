"""Hold-to-talk Mac voice assistant: Whistle turns speech into text, Needle picks a tool, the tool runs."""

import queue
import re
import subprocess
import sys

import needle
import numpy as np
import sounddevice as sd
from pynput import keyboard

import whatsapp


SAMPLE_RATE = 16000
MIN_CONFIDENCE = 0.3
ALWAYS_SAFE_TOOLS = {"open_settings", "send_whatsapp"}
MIN_SECONDS = 0.3
TALK_KEY = keyboard.Key.alt_r

APP_ALIASES = {
    "settings": "System Settings",
    "system settings": "System Settings",
    "system preferences": "System Settings",
    "browser": "Safari",
    "chrome": "Google Chrome",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "terminal": "Terminal",
    "finder": "Finder",
    "music": "Music",
    "notes": "Notes",
    "calendar": "Calendar",
    "mail": "Mail",
}

SETTINGS_PANES = {
    "wifi": "com.apple.wifi-settings-extension",
    "wi-fi": "com.apple.wifi-settings-extension",
    "bluetooth": "com.apple.BluetoothSettings",
    "sound": "com.apple.Sound-Settings.extension",
    "display": "com.apple.Displays-Settings.extension",
    "displays": "com.apple.Displays-Settings.extension",
    "battery": "com.apple.Battery-Settings.extension",
    "network": "com.apple.Network-Settings.extension",
    "general": "com.apple.systempreferences.GeneralSettings",
    "keyboard": "com.apple.Keyboard-Settings.extension",
    "privacy": "com.apple.settings.PrivacySecurity.extension",
}

KEYWORDS = ["Safari", "System Settings", "Bluetooth", "Wi-Fi", "Finder", "VS Code", "WhatsApp"]

ORDINALS = {
    "first": 0, "one": 0, "1": 0,
    "second": 1, "two": 1, "2": 1,
    "third": 2, "three": 2, "3": 2,
    "fourth": 3, "four": 3, "4": 3,
    "fifth": 4, "five": 4, "5": 4,
}
YES_WORDS = {"yes", "yeah", "yep", "send", "ok", "okay", "sure", "haan", "correct"}
NO_WORDS = {"no", "nope", "cancel", "stop", "dont", "don't"}
MAX_NAMES_SPOKEN = 5

pending: dict | None = None


def run(command: list[str]) -> bool:

    """Run a shell command and report whether it succeeded."""

    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  command failed: {result.stderr.strip()}")

    return result.returncode == 0


def speak(text: str) -> None:

    """Say a short reply out loud without blocking the assistant."""

    print(f"  > {text}")
    subprocess.Popen(["say", text])


def osascript(script: str) -> bool:

    """Run one line of AppleScript."""

    return run(["osascript", "-e", script])


@needle.tool
def open_app(name: str):

    """Open or launch an application on the Mac by its name, like Safari, Finder or Notes."""

    app = APP_ALIASES.get(name.lower().strip(), name)

    if app == "System Settings":
        return open_settings("")

    if run(["open", "-a", app]):
        speak(f"Opening {app}")
        return app

    speak(f"I could not find {app}")

    return None


@needle.tool
def open_settings(pane: str):

    """Open System Settings, optionally on a page like wifi, bluetooth, sound, display or battery."""

    pane_id = SETTINGS_PANES.get(pane.lower().strip())

    if pane_id:
        run(["open", f"x-apple.systempreferences:{pane_id}"])
        speak(f"Opening {pane} settings")
        return pane

    run(["open", "-a", "System Settings"])
    speak("Opening settings")

    return "settings"


@needle.tool
def set_volume(level: int):

    """Set the Mac speaker volume to a number from 0 to 100."""

    level = max(0, min(100, level))
    osascript(f"set volume output volume {level}")
    speak(f"Volume {level}")

    return level


@needle.tool
def mute():

    """Mute the Mac sound."""

    osascript("set volume output muted true")

    return "muted"


@needle.tool
def unmute():

    """Unmute the Mac sound."""

    osascript("set volume output muted false")
    speak("Sound on")

    return "unmuted"


@needle.tool
def lock_screen():

    """Lock the Mac screen."""

    run(["pmset", "displaysleepnow"])

    return "locked"


@needle.tool
def set_dark_mode(enabled: bool):

    """Turn dark mode on or off."""

    state = "true" if enabled else "false"
    osascript(f'tell application "System Events" to tell appearance preferences to set dark mode to {state}')
    speak("Dark mode on" if enabled else "Light mode on")

    return enabled


@needle.tool(triggers=[r"\bwhats ?app\b(\s+\S+){2,}", r"\bmessage\b.*\bwhats ?app\b"])
def send_whatsapp(
    contact: str,
    message: str
    ):

    """Send a WhatsApp message to a person. contact is the person's name, message is the text to send."""

    global pending

    people = whatsapp.find_contacts(contact)

    if not people:
        speak(f"I could not find {contact} in WhatsApp")
        return None

    if len(people) == 1:
        return confirm_send(people[0], message)

    pending = {"step": "choose", "people": people, "message": message}
    ask_which(people)

    return "asking"


def ask_which(people: list[dict]) -> None:

    """Read out the matching names and ask which person to message."""

    names = [person["name"] for person in people[:MAX_NAMES_SPOKEN]]

    for number, name in enumerate(names, start=1):
        print(f"    {number}. {name}")

    if len(people) > MAX_NAMES_SPOKEN:
        speak(f"I found {len(people)} people. Say the full name.")
        return

    speak(f"I found {', '.join(names[:-1])} and {names[-1]}. Which one?")


def confirm_send(
    person: dict,
    message: str
    ) -> str:

    """Ask for a yes before sending, so a wrong guess never goes out."""

    global pending

    pending = {"step": "confirm", "person": person, "message": message}
    speak(f"Send {message} to {person['name']}?")

    return "confirming"


def answer_pending(text: str) -> None:

    """Use the user's reply to pick a person or to confirm the send."""

    global pending

    words = re.findall(r"[a-z0-9']+", text.lower())
    state = pending
    pending = None

    if NO_WORDS & set(words):
        speak("Cancelled")
        return

    if state["step"] == "confirm":

        if YES_WORDS & set(words):
            sent = whatsapp.send(state["person"]["phone"], state["message"])
            speak(f"Sent to {state['person']['name']}" if sent else "I could not send it")
            return

        speak("Cancelled")
        return

    people = state["people"]
    picked = [people[ORDINALS[word]] for word in words if word in ORDINALS and ORDINALS[word] < len(people)][:1]

    if not picked:
        picked = [person for person in people if any(len(word) > 2 and word in person["name"].lower() for word in words)]

    if len(picked) == 1:
        confirm_send(picked[0], state["message"])
        return

    pending = {"step": "choose", "people": picked or people, "message": state["message"]}
    ask_which(pending["people"])


TOOLS = [open_app, open_settings, set_volume, mute, unmute, lock_screen, set_dark_mode, send_whatsapp]
TOOLS_BY_NAME = {tool.__name__: tool for tool in TOOLS}


def handle(
    agent: needle.Needle,
    text: str
    ) -> None:

    """Ask Needle which tool fits the text, then run it if Needle is confident enough."""

    print(f'you said: "{text}"')

    if not text.strip():
        return

    if pending:
        answer_pending(text)
        return

    reply = agent.complete(text)
    calls = reply.get("function_calls") or []
    confidence = reply.get("confidence") or 0.0
    print(f"  needle: {calls} (confidence {confidence:.2f})")

    if not calls:
        speak("Sorry, I can't do that yet")
        return

    if confidence < MIN_CONFIDENCE and calls[0]["name"] not in ALWAYS_SAFE_TOOLS:
        speak("Sorry, please say that again")
        return

    for call in calls:
        tool = TOOLS_BY_NAME.get(call["name"])

        if tool:
            tool(**call.get("arguments", {}))


def listen(agent: needle.Needle) -> None:

    """Record while the talk key is held, then transcribe and handle the command on release."""

    chunks: list[np.ndarray] = []
    recordings: queue.Queue = queue.Queue()
    talking = False

    def on_audio(
        data,
        frames,
        time,
        status
        ):

        """Keep audio only while the talk key is held."""

        if talking:
            chunks.append(data[:, 0].copy())

    def on_press(key):

        """Start a new recording when the talk key goes down."""

        nonlocal talking

        if key == TALK_KEY and not talking:
            chunks.clear()
            talking = True
            print("listening...")

    def on_release(key):

        """Stop recording when the talk key comes up and queue the audio."""

        nonlocal talking

        if key == TALK_KEY and talking:
            talking = False
            recordings.put(np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32))

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
    hotkeys = keyboard.Listener(on_press=on_press, on_release=on_release)

    with stream, hotkeys:
        print("Ready. Hold the right Option key and speak. Ctrl+C to quit.")

        while True:
            audio = recordings.get()

            if len(audio) < SAMPLE_RATE * MIN_SECONDS:
                print("  too short, hold the key while you speak")
                continue

            result = needle.transcribe(audio, language="en", keywords=KEYWORDS)
            handle(agent, result["text"])


def main() -> None:

    """Start the assistant, handle one typed command with --text, or type commands with --chat."""

    agent = needle.Needle(tools=TOOLS, stateless=True)

    if len(sys.argv) > 2 and sys.argv[1] == "--text":
        handle(agent, " ".join(sys.argv[2:]))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--chat":
        print("Type a command. Ctrl+C to quit.")

        try:
            while True:
                handle(agent, input("> "))

        except (KeyboardInterrupt, EOFError):

            print("\nbye")

        return

    try:
        listen(agent)

    except KeyboardInterrupt:

        print("\nbye")


if __name__ == "__main__":
    main()
