#include <Servo.h>

struct ButtonServo {
  const char *name;
  int pin;
  int restAngle;
  int pressAngle;
  Servo servo;
  bool attached;
};

ButtonServo buttons[] = {
  {"SETUP", 3, 20, 68},
  {"EFFECT", 5, 20, 68},
  {"ROOT", 6, 20, 68},
  {"SCALE", 9, 20, 68},
};

const int buttonCount = sizeof(buttons) / sizeof(buttons[0]);

ButtonServo *findButton(String name) {
  name.trim();
  name.toUpperCase();
  for (int i = 0; i < buttonCount; i++) {
    if (name == buttons[i].name) {
      return &buttons[i];
    }
  }
  return nullptr;
}

void setButton(ButtonServo &button, bool pressed) {
  button.servo.write(pressed ? button.pressAngle : button.restAngle);
}

void pressButton(ButtonServo &button, int durationMs) {
  setButton(button, true);
  delay(durationMs);
  setButton(button, false);
}

void allRest() {
  for (int i = 0; i < buttonCount; i++) {
    setButton(buttons[i], false);
  }
}

void enterCalibrationSetupEffect() {
  ButtonServo *setup = findButton("SETUP");
  ButtonServo *effect = findButton("EFFECT");
  if (!setup || !effect) {
    Serial.println("ERR missing setup/effect");
    return;
  }

  setButton(*setup, true);
  delay(2300);
  setButton(*setup, false);
  delay(450);
  pressButton(*effect, 250);
  Serial.println("OK CAL_SETUP_EFFECT");
}

void enterCalibrationSetupRoot() {
  ButtonServo *setup = findButton("SETUP");
  ButtonServo *root = findButton("ROOT");
  if (!setup || !root) {
    Serial.println("ERR missing setup/root");
    return;
  }

  setButton(*setup, true);
  delay(250);
  pressButton(*root, 250);
  delay(100);
  setButton(*setup, false);
  Serial.println("OK CAL_SETUP_ROOT");
}

void handleLine(String line) {
  line.trim();
  if (!line.length()) {
    return;
  }

  int firstSpace = line.indexOf(' ');
  String command = firstSpace < 0 ? line : line.substring(0, firstSpace);
  String rest = firstSpace < 0 ? "" : line.substring(firstSpace + 1);
  command.toUpperCase();

  if (command == "REST") {
    allRest();
    Serial.println("OK REST");
    return;
  }

  if (command == "CAL") {
    enterCalibrationSetupEffect();
    return;
  }

  if (command == "CALROOT") {
    enterCalibrationSetupRoot();
    return;
  }

  int secondSpace = rest.indexOf(' ');
  String name = secondSpace < 0 ? rest : rest.substring(0, secondSpace);
  String value = secondSpace < 0 ? "" : rest.substring(secondSpace + 1);
  ButtonServo *button = findButton(name);
  if (!button) {
    Serial.println("ERR unknown button");
    return;
  }

  if (command == "P") {
    int durationMs = value.length() ? value.toInt() : 250;
    pressButton(*button, constrain(durationMs, 50, 10000));
    Serial.print("OK P ");
    Serial.println(button->name);
    return;
  }

  if (command == "H") {
    setButton(*button, true);
    Serial.print("OK H ");
    Serial.println(button->name);
    return;
  }

  if (command == "R") {
    setButton(*button, false);
    Serial.print("OK R ");
    Serial.println(button->name);
    return;
  }

  if (command == "ANGLE") {
    int split = value.indexOf(' ');
    if (split < 0) {
      Serial.println("ERR angle needs rest press");
      return;
    }
    button->restAngle = constrain(value.substring(0, split).toInt(), 0, 180);
    button->pressAngle = constrain(value.substring(split + 1).toInt(), 0, 180);
    setButton(*button, false);
    Serial.print("OK ANGLE ");
    Serial.println(button->name);
    return;
  }

  Serial.println("ERR unknown command");
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < buttonCount; i++) {
    buttons[i].servo.attach(buttons[i].pin);
    buttons[i].attached = true;
  }
  allRest();
  Serial.println("READY Theremini remote buttons");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    handleLine(line);
  }
}
