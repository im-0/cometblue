# cometblue
## What is it?
"Comet Blue" is "The intelligent Bluetooth enabled energy-saving radiator thermostat", as stated in official documentation. Link to manufacturer's web page: http://www.eurotronic.org/produkte/comet-blue.html.

From the software point of view, "Comet Blue" is an BLE (Bluetooth Low Energy) device that implements GATT (Generic Attribute Profile Specification).

This project provides python library and command line tool which may be used to control "Comet Blue" from any linux system equipped with Bluetooth adapter (USB Bluetooth dongle, for example).

## Installation
From sources:
```
# Install dependencies
pip install -r requirements.txt
# Install cometblue
python setup.py install
```
Using *pip*:
```
pip install cometblue
```

## Command line tool
### Generic options
```
Usage: cometblue [OPTIONS] COMMAND [ARGS]...

  Command line tool for "Comet Blue" radiator thermostat

Options:
  -a, --adapter TEXT              Bluetooth adapter interface  [default: hci0]
  -f, --formatter [json|human-readable|shell-var]
                                  Output formatter  [default: human-readable]
  -L, --log-level TEXT            [default: error]
  -h, --help                      Show this message and exit.
```

### Device discovery
```
Usage: cometblue discover [OPTIONS]

  Discover "Comet Blue" Bluetooth LE devices

Options:
  -t, --timeout INTEGER  Device discovery timeout in seconds  [default: 10]
  -h, --help             Show this message and exit.
```
*cometblue* automatically filters device list and shows only supported devices.

Usage example:
```
# cometblue discover
Comet Blue (E0:E5:CF:D6:98:53)
```

### Device functions
```
Usage: cometblue device [OPTIONS] ADDRESS COMMAND [ARGS]...

  Get or set values

Options:
  -p, --pin TEXT       PIN for connecting to device (factory default PIN is 0)
  -P, --pin-file TEXT  Read PIN for connecting to device from file
  -h, --help           Show this message and exit.

Commands:
  backup   Backup all supported configuration values in...
  get      Get value
  restore  Restore configuration values from backup in...
  set      Set value (always requires PIN)
```

#### Get values
```
Usage: cometblue device get [OPTIONS] COMMAND [ARGS]...

  Get value

Options:
  -h, --help  Show this message and exit.

Commands:
  battery             Get battery charge (requires PIN)
  datetime            Get time and date (requires PIN)
  days                Get configured periods per days of the week...
  device_name         Get device name
  firmware_revision   Get firmware revision
  firmware_revision2  Get firmware revision #2 (requires PIN)
  flags               Get flags (requires PIN)
  holidays            Get configured holidays (requires PIN)
  lcd_timer           Get LCD timer (requires PIN)
  manufacturer_name   Get manufacturer name
  model_number        Get model number
  software_revision   Get software revision
  temperatures        Get temperatures (requires PIN)
```
Usage examples:
```
# cometblue device -p 0 E0:E5:CF:D6:98:53 get battery
39%

# cometblue device -p 0 E0:E5:CF:D6:98:53 get datetime
2016-03-27 18:32:00

# cometblue device -p 0 E0:E5:CF:D6:98:53 get temperatures
Current temperature: 23.0 °C
Temperature for manual mode: 16.0 °C
Target temperature low: 16.0 °C
Target temperature high: 21.0 °C
Offset temperature: 0.0 °C
Window open detection: 4
Window open minutes: 10

# cometblue device E0:E5:CF:D6:98:53 get device_name  # no PIN required
Comet Blue

# cometblue device E0:E5:CF:D6:98:53 get firmware_revision  # no PIN required
COBL0126

# cometblue device E0:E5:CF:D6:98:53 get software_revision  # no PIN required
0.0.6-sygonix1
```

#### Set values
```
Usage: cometblue device set [OPTIONS] COMMAND [ARGS]...

  Set value (always requires PIN)

Options:
  -h, --help  Show this message and exit.

Commands:
  datetime      Set time and date (requires PIN)
  day           Set periods per days of the week (requires...
  holiday       Set period and temperature for holiday...
  lcd_timer     Set LCD timer (requires PIN)
  pin           Set PIN (requires PIN)
  temperatures  Set temperatures (requires PIN)
```
Usage examples:
```
# cometblue device -p 0 E0:E5:CF:D6:98:53 set datetime  # set time and date on device to current time and date

# cometblue device -p 0 E0:E5:CF:D6:98:53 set datetime "2014-08-27 12:23:56"  # set time and date on device to some specific value

# cometblue device -p 0 E0:E5:CF:D6:98:53 set pin 123  # change PIN from factory default ("0") to new one ("123")
```

##### Changing per day time periods
```
Usage: cometblue device set day [OPTIONS] DAY [PERIOD]...

  Set periods per days of the week (requires PIN)

Options:
  -h, --help  Show this message and exit.
```

Usage examples:
```
# cometblue device -p 0 E0:E5:CF:D6:98:53 set day wed  # clear settings for Wednsday

# cometblue device -p 0 E0:E5:CF:D6:98:53 set day sun -- "-04:00:00" "07:30:00-14:50:00" "21:00:00-"  # set three periods for Sunday
```
Day may be specified as a full weekday name, short weekday name or number (*"monday"* == *"mon"* == *"1"*). Up to four periods may be specified per day. Each period should be in one of following formats:
 - *"-MM:HH:SS"* - from the beginning of day (*00:00:00*) to *MM:HH:SS*.
 - *"mm:hh:ss-MM:HH:SS"*
 - *"mm:hh:ss-"* - from *mm:hh:ss* to the the end of day (23:59:59).

##### Changing holidays
```
Usage: cometblue device set holiday [OPTIONS] HOLIDAY [START] [END]
                                    [TEMPERATURE]

  Set period and temperature for holiday (requires PIN)

Options:
  -h, --help  Show this message and exit.
```
Usage example:
```
# cometblue device -p 0 E0:E5:CF:D6:98:53 set holiday 4 "2015-12-31 00:00:00" "2016-01-14 18:00:00" 23.5   # define holiday 4

# cometblue device -p 0 E0:E5:CF:D6:98:53 set holiday 4  # clear settings for holiday 4
```
Up to eight holidays supported.

#### Backup and restore
```
Usage: cometblue device backup [OPTIONS] [FILE_NAME]

  Backup all supported configuration values in JSON format to file or stdout

Options:
  -h, --help  Show this message and exit.

Usage: cometblue device restore [OPTIONS] [FILE_NAME]

  Restore configuration values from backup in JSON format (from file or
  stdin)

Options:
  -h, --help  Show this message and exit.
```
Usage example:
```
# cometblue device -p 0 E0:E5:CF:D6:98:53 backup ./backup.json  # backup current settings

# cometblue device -p 0 E0:E5:CF:D6:98:53 restore ./backup.json  # restore settings from backup
```

## Links
- http://torsten-traenkner.de/wissen/smarthome/heizung.php

## TODO
- Support flags
- Support timer
- Write tests
- Python3

## Notes
Tool and library may not work as expected because it is not well tested. Patches and bugreports are always welcome.
