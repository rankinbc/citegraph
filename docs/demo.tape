Output docs/demo.gif
Set FontSize 16
Set Width 1200
Set Height 700
Set TypingSpeed 40ms

Type "citegraph query what_calls symbol=flask:flask.json.loads --root ~/.citegraph/corpus/repos --name eval-corpus | head -40"
Enter
Sleep 4s
Type "claude -p 'Which functions call flask.json.loads, and how sure are you? Cite file:line.' --mcp-config docs/demo-mcp.json --allowedTools 'mcp__citegraph__*'"
Enter
Sleep 30s
