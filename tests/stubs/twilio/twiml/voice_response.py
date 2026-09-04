class VoiceResponse:
    def __init__(self): self.parts=[]
    def say(self,t,**k): self.parts.append(("say",t))
    def append(self,g): self.parts.append(("gather",g))
    def redirect(self,u,**k): self.parts.append(("redirect",u))
    def record(self,**k): self.parts.append(("record",k))
    def hangup(self): self.parts.append(("hangup",None))
    def __str__(self): return str(self.parts)
class Gather:
    def __init__(self,**k): self.kw=k; self.said=""
    def say(self,t,**kw): self.said=t
