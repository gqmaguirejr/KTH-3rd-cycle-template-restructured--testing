-- kth/kth-metadata.lua

-- 1. Define the module and a local table to hold its functions.
local KTHMetadata = {}

-- 2. Initialize the main data table. This is the central repository for all thesis info.
KTHMetadata.thesisData = {
  abstracts = {},
  keywords = {}
}

-- 3. Define the function to set simple key-value pairs.
-- It can handle nested keys, e.g., set_data({"Author1", "Last name"}, "Student")
function KTHMetadata.set_data(keys, value)
  local t = KTHMetadata.thesisData
  for i = 1, #keys - 1 do
    local key = keys[i]
    if not t[key] then t[key] = {} end
    t = t[key]
  end
  t[keys[#keys]] = value
end

-- This function takes a macro name as a string and robustly gets its value from TeX.
function KTHMetadata.set_data_from_macro(keys, macroname)
  local value = tex.get_macro(macroname)
  KTHMetadata.set_data(keys, value)
end

-- 4. Define the function to add data from temporary files (for abstracts, etc.).
function KTHMetadata.add_data_from_file(category, lang, filename)
  local file = io.open(filename, "r")
  if not file then return end
  local content = file:read("*a")
  file:close()
  
  if not KTHMetadata.thesisData[category] then KTHMetadata.thesisData[category] = {} end
  KTHMetadata.thesisData[category][lang] = content
end

-- 5. Define the function to write the final JSON file.
--    Note that this places the third-party library in the kth folder
--    as the kth-metadata.lua code is the only code that will use it.
function KTHMetadata.write_json_file()
  -- This requires dkjson.lua to be available in a place Lua can find it.
  local dkjson = require("kth.dkjson")
  -- local dkjson = require("dkjson")
  
  -- You can define your key order here
  local keyorder = {
  "Author1", "Author2", "Course Info", "Degree1", "Degree2", "Title", "Alternative title", "Title in plain text", "Alternative title in plain text",
  "Supervisor1", "Supervisor2", "Supervisor3", "Supervisor4", "Supervisor5", 
  "Opponents",
  "National Subject Categories", "SDGs",
  "Other information", "Series", "ISBN", "Copyrightleft", "Presentation", 
  "abstracts", "keywords"
  }

  
  local jsonString = dkjson.encode(KTHMetadata.thesisData, { indent = true, keyorder = keyorder })
  
  local outputFile = io.open("fordiva.json", "w")
  if outputFile then
    outputFile:write(jsonString)
    outputFile:close()
  end
end

-- 6. Return the module's public functions at the end of the file.
return KTHMetadata
