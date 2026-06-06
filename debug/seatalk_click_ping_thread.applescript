on envNumber(name, defaultValue)
	set rawValue to system attribute name
	if rawValue is missing value or rawValue is "" then return defaultValue
	try
		return rawValue as number
	on error
		return defaultValue
	end try
end envNumber

set iconRightOffset to envNumber("SEATALK_THREAD_ICON_RIGHT", 291)
set iconTopOffset to envNumber("SEATALK_THREAD_ICON_TOP", 31)
set rowRightOffset to envNumber("SEATALK_THREAD_ROW_RIGHT", 320)
set rowTopOffset to envNumber("SEATALK_THREAD_ROW_TOP", 160)
set openDelay to envNumber("SEATALK_THREAD_OPEN_DELAY", 0.8)
set clickHelper to system attribute "SEATALK_CLICK_HELPER"
if clickHelper is missing value or clickHelper is "" then set clickHelper to "/private/tmp/radar_seatalk_ping_helper"

try
	tell application id "com.seagroup.seatalkmac.enterprise" to activate
on error
	tell application "SeaTalk" to activate
end try
delay 0.8

tell application "System Events"
	tell process "SeaTalk"
		set frontmost to true
		set seatalkWindow to first window
		perform action "AXRaise" of seatalkWindow
		set {windowX, windowY} to position of seatalkWindow
		set {windowWidth, windowHeight} to size of seatalkWindow
	end tell
	repeat with waitIndex from 1 to 20
		if frontmost of process "SeaTalk" then exit repeat
		delay 0.1
	end repeat
	set frontAppName to name of first application process whose frontmost is true
end tell

set iconX to windowX + windowWidth - iconRightOffset
set iconY to windowY + iconTopOffset
set rowX to windowX + windowWidth - rowRightOffset
set rowY to windowY + rowTopOffset

if frontAppName is not "SeaTalk" then
	error "SeaTalk is not frontmost before click; frontmost app is " & frontAppName
end if

log "SeaTalk window position {" & windowX & ", " & windowY & "} size {" & windowWidth & ", " & windowHeight & "}"
log "frontmost app before click: " & frontAppName
log "clicking SeaTalk thread icon at {" & iconX & ", " & iconY & "}"
do shell script quoted form of clickHelper & " click-point " & iconX & " " & iconY
delay openDelay

log "clicking first @You thread row at {" & rowX & ", " & rowY & "}"
do shell script quoted form of clickHelper & " click-point " & rowX & " " & rowY
