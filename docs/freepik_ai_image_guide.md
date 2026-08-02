> **Obsolete.** Freepik was removed as an image provider (persistent 401 from
> an expired key, and only ever a one-time trial credit rather than an
> ongoing free tier). Cloudflare Workers AI is the current primary image
> generator — see `docs/service_setup.md` section 5. Kept below for
> historical reference only.

Flux dev
Create image from text - Flux dev
Convert descriptive text input into images using AI. This endpoint accepts a variety of parameters to customize the generated images.

POST
/
v1
/
ai
/
text-to-image
/
flux-dev

Try it
Authorizations
​
x-freepik-api-key
stringheaderrequired
Your Freepik API key. Required for authentication. Learn how to obtain an API key

Body
application/json


Example:
`
import requests

url = "https://api.freepik.com/v1/ai/text-to-image/flux-dev"

payload = {
    "prompt": "<string>",
    "webhook_url": "https://www.example.com/webhook",
    "aspect_ratio": "square_1_1",
    "styling": {
        "effects": {
            "color": "softhue",
            "framing": "portrait",
            "lightning": "iridescent"
        },
        "colors": [
            {
                "color": "#FF0000",
                "weight": 0.5
            }
        ]
    },
    "seed": 2147483648
}
headers = {
    "x-freepik-api-key": "<api-key>",
    "Content-Type": "application/json"
}

response = requests.post(url, json=payload, headers=headers)

print(response.json())
`
​
prompt
string
The prompt is a short text that describes the image you want to generate. It can range from simple descriptions, like "a cat", to detailed scenarios, such as "a cat with wings, playing the guitar, and wearing a hat". If no prompt is provided, the AI will generate a random image.

​
webhook_url
string<uri>
Optional callback URL that will receive asynchronous notifications whenever the task changes status. The payload sent to this URL is the same as the corresponding GET endpoint response, but without the data field.

Example:
"https://www.example.com/webhook"

​
aspect_ratio
enum<string>default:square_1_1
Image size with the aspect ratio. The aspect ratio is the proportional relationship between an image's width and height, expressed as *_width_height (e.g., square_1_1, widescreen_16_9). It is calculated by dividing the width by the height.

If not present, the default is square_1_1.

Available options: square_1_1, classic_4_3, traditional_3_4, widescreen_16_9, social_story_9_16, standard_3_2, portrait_2_3, horizontal_2_1, vertical_1_2, social_post_4_5 
Example:
"square_1_1"

​
styling
object
Styling options for the image

Show child attributes

​
seed
integer
Seed for the image generation. If not provided, a random seed will be used.

Required range: 1 <= x <= 4294967295
Response

200

application/json
OK - Get the status of the flux-dev task

​
data
objectrequired
Show child attributes

Example:
{
  "task_id": "046b6c7f-0b8a-43b9-b35d-6489e6daee91",
  "status": "CREATED"
}



Get the status of the flux-dev task
Get the status of the flux-dev task

GET
/
v1
/
ai
/
text-to-image
/
flux-dev
/
{task-id}

Try it
Authorizations
​
x-freepik-api-key
stringheaderrequired
Your Freepik API key. Required for authentication. Learn how to obtain an API key

Path Parameters
​
task-id
stringrequired
ID of the task

exmple: 
`
import requests

url = "https://api.freepik.com/v1/ai/text-to-image/flux-dev/{task-id}"

headers = {"x-freepik-api-key": "<api-key>"}

response = requests.get(url, headers=headers)

print(response.json())
`

Response

200

application/json
OK - The task exists and the status is returned

​
data
objectrequired
Show child attributes

Example:
{
  "generated": [
    "https://openapi-generator.tech",
    "https://openapi-generator.tech"
  ],
  "task_id": "046b6c7f-0b8a-43b9-b35d-6489e6daee91",
  "status": "CREATED"
}


Get the status of the flux-dev task
Get the status of the flux-dev task

GET
/
v1
/
ai
/
text-to-image
/
flux-dev

Try it
Authorizations
​
x-freepik-api-key
stringheaderrequired
Your Freepik API key. Required for authentication. Learn how to obtain an API key


example:
`
import requests

url = "https://api.freepik.com/v1/ai/text-to-image/flux-dev"

headers = {"x-freepik-api-key": "<api-key>"}

response = requests.get(url, headers=headers)

print(response.json())
`

Response

200

application/json
OK - Get the status of all flux-dev tasks

​
data
object[]required
Hide child attributes

​
task_id
string<uuid>required
Task identifier

​
status
enum<string>required
Task status

Available options: CREATED, IN_PROGRESS, COMPLETED, FAILED 