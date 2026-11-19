import backup_probe
import llm
import re
import traceback

def dump(args):
   print("OpenWhisk context variables:")
   for key in args.keys():
      if key.startswith("__"):
          print(f"{key}: {args[key]}")

def chat(args):
  inp = args.get("input", "")
  command = backup_probe.command(inp)

  try:
    if command:
      out = llm.stream_output(args, backup_probe.reply(args, command))
    else:
      ai = llm.LLM(args)
      if inp == "":
        out = ai.welcome(args)
      elif inp == "@":
        out = ai.rag(args)
      else:
        out = ai.ask(args, inp)

  except Exception as e:
    traceback.print_exc()
    out = f"Error: {str(e)}\n"

  return { "output": out, "streaming": True}

