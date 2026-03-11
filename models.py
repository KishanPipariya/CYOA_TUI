from pydantic import BaseModel, Field
from typing import List, Optional

class Choice(BaseModel):
    text: str = Field(description="The description of the action the user can take.")

class StoryNode(BaseModel):
    title: Optional[str] = Field(default=None, description="The generated title for this story adventure. (Only necessary for the very first node of the game).")
    narrative: str = Field(description="The unfolding story text describing what just happened and the current situation.")
    choices: List[Choice] = Field(description="A list of 2 to 4 choices for the user's next action.")
