%% forked from https://gits-15.sys.kth.se/giampi/kthlatex kthlatex-0.2rc4 on 2020-02-13
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{kththesis}[2025/05/25 KTH 3rd cycle Thesis formatting]

\input{./kth/kth-options}

% Process all class options.

%% Send any unknown option to the report class
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{report}}

\ProcessOptions\relax


% Now, ACT on the options that affect the base class.
%    This logic MUST be in the .cls file.
\ifgfivepaper
  \LoadClass[10pt]{report}
\else
  \LoadClass[12pt]{report}
\fi

% Load all external packages needed by kththesis.cls
%\RequirePackage{./kth/kth-packages}
\input{./kth/kth-packages}

% include the font cache tools
\input{kth/kth-font-cache}

% Set up fonts
\input{./kth/kth-fonts}

% Define page layout and colors
%\RequirePackage{./kth/kth-layout}
\input{./kth/kth-layout}




% 5. Define all custom commands and environments
%\RequirePackage{./kth/kth-commands}
\input{./kth/kth-commands}

%% Loads the Lua backend. It makes all the Lua functions—like set_data() and write_json_file()—available to the TeX engine.
% This line loads the module and makes the 'KTHMetadata' table
% globally available to all subsequent \directlua calls.
\directlua{KTHMetadata = require("kth.kth-metadata")}
%% Loads the LaTeX frontend. It defines all the user-facing commands and environments—like \SetValue{} and \begin{ThesisAbstract}—that the author uses to provide their data.

%\RequirePackage{./kth/kth-metadata}
\input{./kth/kth-metadata}

% get all of the information about the school codes and program codes
%\input{kth/schools_and_programs_3rd_cycle.ins}
% get school codes and education subject codes
\input{kth/school_subjects_3rdcycle.ins}
\newcommand{\MONTH}{%
  \ifinswedish
  \ifcase\the\month
  \or januari% 1
  \or februari% 2
  \or mars% 3
  \or april% 4
  \or maj% 5
  \or juni% 6
  \or juli% 7
  \or augusti% 8
  \or september% 9
  \or oktober% 10
  \or november% 11
  \or december% 12
  \fi
  \else
  \ifcase\the\month
  \or January% 1
  \or February% 2
  \or March% 3
  \or April% 4
  \or May% 5
  \or June% 6
  \or July% 7
  \or August% 8
  \or September% 9
  \or October% 10
  \or November% 11
  \or December% 12
  \fi
\fi
}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Add time stamps to the log file
% These can be either explicit with: \ReportTimeStamp{message}
% or via a hook on the ship out of each page.
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

\usepackage{luacode}
% --- LUA BACKEND ---
\begin{luacode*}
-- Record start time
compilation_start_time = os.clock()

-- Function for logging an event with a timestamp
function log_event(message)
  local current_time = os.clock()
  local elapsed = current_time - compilation_start_time
  local log_message = string.format("[+%.3fs] %s", elapsed, message)
  tex.sprint("\\wlog{" .. log_message .. "}")
end
\end{luacode*}

% --- LATEX FRONTEND ---
% Command for explicit, manual logging
\newcommand{\ReportTimeStamp}[1]{\directlua{log_event("#1")}}

% Use the modern, built-in hook to log a message for each page
\AddToHook{shipout/before}{\ReportTimeStamp{Shipping out page \thepage}}


% Check that the Figtree font (used for the cover and headings) is present
\AtBeginDocument{
    \IfFontExistsTF{Figtree}{}{
        \IfFontExistsTF{./Figtree/static/Figtree-Regular.ttf}{}{
            \PackageError{KTH template}{Figtree font is missing}{Please install the Figtree font}
        }
    }
}

% Input the sanity checks to be done at the end of the document
\input{kth/kth-sanity-checks}

\endinput
