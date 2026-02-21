-- File: kth/kth-font-cache.lua

local lfs = require("lfs")

local M = {}

-- A simple utility to create a directory if it doesn't exist
local function ensure_dir(path)
  lfs.mkdir(path)
end

-- Recursive function to copy a directory's contents
function M.copy_directory(src, dest)
  ensure_dir(dest)
  for item in lfs.dir(src) do
    if item ~= "." and item ~= ".." then
      local src_path = src .. "/" .. item
      local dest_path = dest .. "/" .. item
      local attrs = lfs.attributes(src_path)
      
      if attrs then
        if attrs.mode == "directory" then
          M.copy_directory(src_path, dest_path) -- Recurse for subdirectories
        else
          -- Copy the file
          local in_file = io.open(src_path, "rb")
          local out_file = io.open(dest_path, "wb")
          if in_file and out_file then
            out_file:write(in_file:read("*a"))
            in_file:close()
            out_file:close()
          end
        end
      end
    end
  end
end

-- Recursive function to link to files in a directory's contents
function M.recursive_link(src, dest)
  ensure_dir(dest)
  for item in lfs.dir(src) do
    if item ~= "." and item ~= ".." then
      local src_path = src .. "/" .. item
      local dest_path = dest .. "/" .. item
      local attrs = lfs.attributes(src_path)
      
      if attrs then
        if attrs.mode == "directory" then
          M.copy_directory(src_path, dest_path) -- Recurse for subdirectories
        else
          -- link the file
          lfs.link(src_path, dest_path, false)
        end
      end
    end
  end
end

-- Define the paths
local temp_cache_dir = "/home/tex/texmf-var/luatex-cache/generic/fonts/otl"
local saved_cache_dir = "./saved_font_cache" -- A folder in your project

function M.my_save_font_cache()
   ensure_dir(saved_cache_dir)
  -- Check if saved_cache_dir exists and is a directory
  local attrs = lfs.attributes(saved_cache_dir)
  if attrs and attrs.mode == "directory" then
    -- copy from font cache to this directory
    M.copy_directory(temp_cache_dir, saved_cache_dir)
        tex.sprint("\\wlog{Copy to ".. saved_cache_dir .." completed}")
  else
    tex.sprint("\\wlog{No local folder ".. saved_cache_dir .." to copy to}")
  end
end


function M.restore_font_cache()  
  local attrs = lfs.attributes(saved_cache_dir)
  if attrs and attrs.mode == "directory" then
    -- copy from font cache to this directory
    M.copy_directory(saved_cache_dir, temp_cache_dir)
    -- M.recursive_link(saved_cache_dir, temp_cache_dir)
    tex.sprint("\\wlog{Copy to ".. saved_cache_dir .." completed}")
  else
    tex.sprint("\\wlog{No local folder ".. saved_cache_dir .." to copy from}")
  end
end

return M
