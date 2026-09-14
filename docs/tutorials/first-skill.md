# Tutorial: Create Your First Skill (5 min)

## Goal
Create a simple text-processing Skill that counts words in a string.

## Steps

1. **Open Skill Forge**
   - Navigate to Console → Skill Forge → "New Skill"

2. **Define Skill**
   ```yaml
   name: word-counter
   description: Count words in input text
   input: string
   output: integer
   ```

3. **Write Logic**
   ```python
   def execute(input: str) -> int:
       return len(input.split())
   ```

4. **Test**
   - Input: "Hello world test"
   - Expected output: 3

5. **Deploy**
   - Click "Save & Deploy"
   - Skill is now available in Marketplace

## Next Steps
- Add error handling
- Create more complex Skills
- Share with team
