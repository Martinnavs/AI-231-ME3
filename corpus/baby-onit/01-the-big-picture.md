# baby-onit: the big picture

baby-onit has only four moving parts, and they talk to each other in exactly one way.

You hand the harness a task. The harness, which is baby_onit.py, wraps your task in a system prompt and sends the messages to the model. The model either answers in plain text, which means it is done, or emits a tool call: a JSON object naming a tool and its arguments. The harness runs that tool, appends the result as a new message, and sends the whole conversation back. The model decides what to do next.

Two properties fall out of this design in baby-onit. First, the model never touches the machine. It can only ask for a tool, and the harness is the only thing that executes anything. This is the entire safety model of the program. Second, the harness never talks to the model except through messages. It is a dumb, faithful relay plus a set of guards. It does not help the model; it just runs what the model asked for and reports back.
