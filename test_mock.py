class MockPM:
    def __init__(self):
        self.traits = {"name": "Global"}
    
    @property
    def name(self):
        return self.traits["name"]

import copy
pm = MockPM()
pm2 = copy.copy(pm)
pm2.traits = {"name": "Local"}

print("Global:", pm.name)
print("Local:", pm2.name)
