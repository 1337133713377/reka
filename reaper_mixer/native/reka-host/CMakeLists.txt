# reka-host.exe : l'hôte de plugins VST2 / VST3 de REKA (Windows 64 bits).
#
#   cmake -S . -B build -A x64
#   cmake --build build --config Release
#
# Dépendances téléchargées au premier build :
#   - interfaces VST3 de Steinberg (pluginterfaces, licence MIT) ;
#   - nlohmann/json (licence MIT).
# Les plugins VST2 sont chargés avec src/vst2_abi.h : pas de SDK VST2.
cmake_minimum_required(VERSION 3.21)
project(reka_host LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Exécutable autonome : pas de dépendance aux redistribuables Visual C++
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

include(FetchContent)
FetchContent_Declare(vst3_pluginterfaces
    GIT_REPOSITORY https://github.com/steinbergmedia/vst3_pluginterfaces.git
    GIT_TAG v3.8.0_build_66
    GIT_SHALLOW TRUE
    SOURCE_DIR ${CMAKE_BINARY_DIR}/vst3/pluginterfaces
    SOURCE_SUBDIR no-cmake)       # dossier inexistant : seulement téléchargé
FetchContent_Declare(nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    URL_HASH SHA256=d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(vst3_pluginterfaces nlohmann_json)

set(PLUGINTERFACES ${CMAKE_BINARY_DIR}/vst3/pluginterfaces)
# Icône de REKA (chemin absolu : rc.exe et windres n'ont pas le même dossier de base)
set(REKA_ICON ${CMAKE_CURRENT_SOURCE_DIR}/../../assets/icon.ico)
cmake_path(NORMAL_PATH REKA_ICON)
configure_file(reka-host.rc.in ${CMAKE_BINARY_DIR}/reka-host.rc @ONLY)
add_executable(reka-host WIN32
    src/main.cpp
    src/util.cpp
    src/voicemeeter.cpp
    src/vst2_plugin.cpp
    src/vst3_plugin.cpp
    src/vst3_iids.cpp
    ${PLUGINTERFACES}/base/funknown.cpp
    ${PLUGINTERFACES}/base/ustring.cpp
    ${PLUGINTERFACES}/base/conststringtable.cpp
    ${CMAKE_BINARY_DIR}/reka-host.rc)
target_include_directories(reka-host PRIVATE src ${CMAKE_BINARY_DIR}/vst3)
target_compile_definitions(reka-host PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN
                           _CRT_SECURE_NO_WARNINGS
                           $<$<CONFIG:Release>:RELEASE=1> $<$<NOT:$<CONFIG:Release>>:DEVELOPMENT=1>)
target_link_libraries(reka-host PRIVATE nlohmann_json::nlohmann_json ws2_32 ole32 shell32
                      advapi32 user32 gdi32)
if(MSVC)
    target_compile_options(reka-host PRIVATE /W3 /utf-8 /EHsc)
else()
    target_link_options(reka-host PRIVATE -municode -static -static-libgcc -static-libstdc++)
endif()

# Plugin VST2 de test (gain), utilisé par les tests de REKA
option(REKA_TEST_PLUGIN "Construire le plugin de test reka-test-gain.dll" ON)
if(REKA_TEST_PLUGIN)
    add_library(reka-test-gain SHARED test/gain_vst2.cpp)
    set_target_properties(reka-test-gain PROPERTIES PREFIX "")
    if(NOT MSVC)
        target_link_options(reka-test-gain PRIVATE -static -static-libgcc -static-libstdc++)
    endif()
endif()

# Faux Voicemeeter (audio callback simulé), pour tester le traitement du son
if(REKA_TEST_PLUGIN)
    add_library(fake-voicemeeter SHARED test/fake_voicemeeter.cpp)
    set_target_properties(fake-voicemeeter PROPERTIES PREFIX "" OUTPUT_NAME "VoicemeeterRemote64"
                          RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/fake-voicemeeter
                          LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/fake-voicemeeter)
    if(MSVC)
        # __stdcall est sans effet en 64 bits : les noms exportés restent nus
    else()
        target_link_options(fake-voicemeeter PRIVATE -static -static-libgcc -static-libstdc++
                            -Wl,--kill-at)
    endif()
endif()
