If $CmdLine[0] < 1 Then
    MsgBox(16, "SeekBot Upload", "Missing file path argument.")
    Exit 1
EndIf

Local $filePath = $CmdLine[1]

If Not FileExists($filePath) Then
    MsgBox(16, "SeekBot Upload", "File not found: " & $filePath)
    Exit 2
EndIf

If Not WinWaitActive("Open", "", 10) Then
    MsgBox(16, "SeekBot Upload", "Open file dialog not found.")
    Exit 3
EndIf

Send($filePath)
Send("{ENTER}")
Exit 0
